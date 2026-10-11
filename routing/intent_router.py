"""规则优先，未命中时最多分类一次；程序决定可执行路径。"""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from agent.core.types import RouteDecision
from prompts.routing import DIRECT_REPLIES, ROUTING_SYSTEM_PROMPT


QUERY_INTENTS = ("inventory_lookup", "price_lookup", "product_detail")
PRODUCT_ID = r"[A-Za-z]{1,8}[0-9]{3,12}"
ID_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])" + PRODUCT_ID + r"(?![A-Za-z0-9_-])")
SOCIAL = {
    "你好": "greeting", "您好": "greeting", "嗨": "greeting",
    "hi": "greeting", "hello": "greeting", "谢谢": "thanks",
    "谢谢你": "thanks", "多谢": "thanks", "再见": "goodbye", "拜拜": "goodbye",
}
QUERY_WORDS = {
    "inventory_lookup": r"库存|有货|有没有货|是否有货|还有货|缺货",
    "price_lookup": r"价格|多少钱|售价|价钱",
    "product_detail": r"参数|配置|规格|详情",
}
# 这些请求需要解释、权衡、额外目标或指定字段，不使用通用单查询模板。
COMPLEX_REQUEST = re.compile(
    r"推荐|比较|对比|性价比|适合|如果|否则|顺便|配件|为什么|原因|同时|并且|以及|和|"
    r"退货|退款|售后|投诉|优惠|折扣|便宜|最低|实时|今天|"
    r"CPU|内存|处理器|屏幕|显卡|硬盘|续航|电池|重量|操作系统",
    re.IGNORECASE,
)
REFERENCE = re.compile(r"它|这款|这个|那款|该商品|刚才|上面|之前")
QUERY_PATTERNS = {
    intent: re.compile(
        r"(?:你好|您好)?[，,！! ]*(?:请)?(?:帮我)?"
        r"(?:查(?:询|一下)?|看(?:看|一下)?|告诉我)?(?:商品)?"
        r"(?P<subject>" + PRODUCT_ID + r"|它|这款|这个|该商品)?(?:的)?"
        r"(?:" + words + r")(?:是多少|多少|怎么样|吗|呢)?",
        re.IGNORECASE,
    )
    for intent, words in QUERY_WORDS.items()
}


class IntentCandidate(BaseModel):
    """分类器的外部输入契约；通过校验也不代表语义分类一定正确。"""

    model_config = ConfigDict(strict=True, extra="forbid")

    intent: Literal[
        "greeting", "thanks", "goodbye", "out_of_scope", "no_actionable_request",
        "inventory_lookup", "price_lookup", "product_detail", "complex_request", "unknown",
    ]
    product_id: str | None
    source: Literal["current_input", "history", "none"]


def product_ids(text):
    return set(ID_PATTERN.findall(text))


class IntentRouter:
    def __init__(self, *, llm):
        self.llm = llm

    def route(self, user_input: str, history: list[dict]) -> RouteDecision:
        """history 不含当前输入，仅从最近十条原文解析指代，不使用压缩摘要猜编号。"""
        recent_history = history[-10:]
        text = user_input.strip().rstrip("。！？!?，, ")
        if not text:
            return self._direct("no_actionable_request", "empty_input")
        social_intent = SOCIAL.get(text.lower())
        if social_intent:
            return self._direct(social_intent, "exact_social")

        mentioned_ids = product_ids(user_input)
        query_kinds = [key for key, pattern in QUERY_WORDS.items() if re.search(pattern, text)]
        if len(mentioned_ids) > 1 or len(query_kinds) > 1 or COMPLEX_REQUEST.search(text):
            return RouteDecision(reason="complex_request", source="rule")
        if text in ("好的", "继续", "就它吧", "好", "行"):
            return RouteDecision(reason="contextual_continuation", source="rule")

        # 对刚刚发出的澄清直接接续；其他裸编号不擅自猜任务。
        if re.fullmatch(PRODUCT_ID, text) and recent_history:
            last_message = recent_history[-1]
            if last_message.get("role") == "assistant":
                for intent in QUERY_INTENTS:
                    if last_message.get("content") == DIRECT_REPLIES[intent]:
                        return self._query(intent, text, "current_input", "rule")

        for intent, pattern in QUERY_PATTERNS.items():
            match = pattern.fullmatch(re.sub(r"\s+", "", text))
            if match is None:
                continue
            subject = match.group("subject")
            if subject and re.fullmatch(PRODUCT_ID, subject):
                if subject in mentioned_ids:
                    return self._query(intent, subject, "current_input", "rule")
                break
            ids = self._history_ids(recent_history)
            if len(ids) == 1:
                return self._query(intent, next(iter(ids)), "history", "rule")
            return self._direct(intent, "missing_or_ambiguous_product")

        try:
            response = self.llm.chat(
                messages=[
                    {"role": "system", "content": ROUTING_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps({
                        "user_input": user_input, "history": recent_history,
                    }, ensure_ascii=False)},
                ],
                tools=None, temperature=0.0, max_tokens=180,
                response_format={"type": "json_object"}, thinking=False,
            )
            if getattr(response, "tool_calls", None):
                raise ValueError("分类器不能调用工具")
            candidate = IntentCandidate.model_validate_json(response.content)
        except Exception:
            return RouteDecision(reason="classifier_failed", source="fallback")
        return self._validate_candidate(candidate, user_input, recent_history)

    def _validate_candidate(self, candidate, user_input, history):
        intent = candidate.intent
        if intent in ("complex_request", "unknown"):
            return RouteDecision(intent=intent, reason=intent, source="classifier")
        explicit_queries = {key for key, pattern in QUERY_WORDS.items() if re.search(pattern, user_input)}
        if explicit_queries and intent not in explicit_queries:
            return RouteDecision(reason="conflicting_intent", source="fallback")
        if intent not in QUERY_INTENTS:
            # 商品编号或业务表达存在时，分类器不能把它短路成闲聊/无关。
            business = r"商品|库存|有货|价格|多少钱|参数|配置|订单|物流|保修|购买|买"
            if product_ids(user_input) or re.search(business, user_input):
                return RouteDecision(reason="conflicting_intent", source="fallback")
            if candidate.product_id is not None or candidate.source != "none":
                return RouteDecision(reason="invalid_parameter_source", source="fallback")
            # 规则已覆盖明确社交；未命中的表达不能靠一次模型判断丢掉上下文目标。
            if intent in ("greeting", "thanks", "goodbye"):
                return RouteDecision(reason="uncertain_social", source="fallback")
            return self._direct(intent, "classified_direct", source="classifier")

        pid = candidate.product_id
        current_ids = product_ids(user_input)
        if pid is None:
            if candidate.source != "none" or current_ids:
                return RouteDecision(reason="invalid_parameter_source", source="fallback")
            return self._direct(intent, "missing_or_ambiguous_product", source="classifier")
        if not re.fullmatch(PRODUCT_ID, pid):
            return RouteDecision(reason="invalid_product_id", source="fallback")
        if candidate.source == "current_input" and current_ids == {pid}:
            return self._query(intent, pid, "current_input", "classifier")
        if candidate.source == "history" and not current_ids and REFERENCE.search(user_input):
            history_ids = self._history_ids(history)
            if history_ids == {pid}:
                return self._query(intent, pid, "history", "classifier")
            if len(history_ids) > 1:
                return self._direct(intent, "missing_or_ambiguous_product", source="classifier")
        return RouteDecision(reason="ungrounded_product_id", source="fallback")

    @staticmethod
    def _history_ids(history):
        ids = set()
        for message in history:
            content = message.get("content", "")
            mentioned = product_ids(content)
            # 否定/取消过的对象不凭“只出现过一个编号”自动沿用。
            if message.get("role") == "user" and mentioned and re.search(r"不要|不用|别查|不查|取消|排除", content):
                return set()
            ids.update(mentioned)
        return ids

    @staticmethod
    def _direct(intent, reason, source="rule"):
        return RouteDecision(
            kind="direct", intent=intent, reply=DIRECT_REPLIES[intent],
            reason=reason, source=source,
        )

    @staticmethod
    def _query(intent, product_id, parameter_source, source):
        return RouteDecision(
            kind="workflow", intent=intent, params={"product_id": product_id},
            parameter_source=parameter_source, reason="single_product_query", source=source,
        )
