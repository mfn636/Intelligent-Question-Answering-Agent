"""
会话入口：管理历史、上下文、本轮状态、用量与用户侧返回结果。
创建状态、组织执行、完成收尾：组装初始上下文，调用执行器，
保存最终回答和历史
"""
import logging
import time
from dataclasses import asdict

from agent.core.state import AgentState
from agent.core.types import RouteDecision, RunTrace, TurnResult
from agent.memory.history import ConversationHistory
from prompts.memory import SUMMARY_CONTEXT_PREFIX


logger = logging.getLogger(__name__)


class ConversationRunner:
    # 批量摘要后保留的近期原文上限；完整问答边界可能使实际条数更少。
    WINDOW = 10
    # 累积到阈值以上才摘要；此前发送全部未压缩原文，避免遗漏中间对话。
    SUMMARY_TRIGGER = 20

    def __init__(self, *, executor, llm, summarizer, session_id: str = "default",
                 system_prompt: str = "", router=None, workflows=None):
        self.executor = executor
        self.llm = llm
        self.summarizer = summarizer
        self.session_id = session_id
        self.system_prompt = system_prompt
        self.router = router
        self.workflows = workflows
        self.history = ConversationHistory()
        self.state = AgentState()

    @property
    def last_trace(self) -> RunTrace:
        """提供当前状态持有的轨迹，供调试和评测使用。"""
        return self.state.trace

    def run(self, user_input: str) -> TurnResult:
        """执行一轮对话；历史只记录用户输入和最终回答，各追加一次。"""
        started = time.perf_counter()
        usage_mark = len(getattr(self.llm, "usage_log", []))
        recent_history = [dict(message) for message in self.history.messages[-10:]]
        self.history.add_user(user_input)
        state = AgentState(
            user_input=user_input,
            status="running",
        )
        self.state = state

        try:
            routing_started = time.perf_counter()
            decision = self.router.route(user_input, recent_history) if self.router else RouteDecision()
            if decision.kind == "workflow" and (
                self.workflows is None or not self.workflows.supports(decision.intent)
            ):
                decision = RouteDecision(reason="workflow_unavailable", source="fallback")
            state.trace.route = asdict(decision)
            state.trace.route["latency_ms"] = round((time.perf_counter() - routing_started) * 1000, 1)
            # 快捷路径不为摘要额外请求模型；待进入 Agent 时统一压缩累计原文。
            if decision.kind == "agent":
                self._compact_history()
            state.messages = [{"role": "system", "content": self.system_prompt}]
            if self.history.summary:
                # 摘要作为历史背景，保持 assistant 角色，不提升为系统指令。
                state.messages.append({
                    "role": "assistant",
                    "content": SUMMARY_CONTEXT_PREFIX + self.history.summary,
                })
            state.messages.extend(dict(message) for message in self.history.messages)
            if decision.kind == "direct":
                reply = decision.reply
            elif decision.kind == "workflow":
                reply = self.workflows.execute(decision, state)
            else:
                reply = self.executor.execute(state)
            self.history.add_assistant(reply)
            state.messages.append({"role": "assistant", "content": reply})
            state.final_replies.append(reply)
            result = TurnResult(
                replies=list(state.final_replies),
                usage=self._collect_usage(usage_mark),
            )
            result.usage["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        except Exception:
            # 保留本轮状态与已有工具轨迹，原异常交由调用方处理
            state.status = "failed"
            raise

        state.status = "completed"
        return result

    def _compact_history(self):
        try:
            self.history.compact(
                self.summarizer, keep=self.WINDOW, trigger=self.SUMMARY_TRIGGER,
            )
        except Exception:
            # 本轮继续使用旧摘要和全部未压缩原文；下次触发时再尝试。
            logger.warning("会话摘要生成失败，保留旧摘要与原始对话继续回答。")

    def _collect_usage(self, mark):
        """汇总本轮执行期间的模型用量，不计入此前轮次。"""
        entries = getattr(self.llm, "usage_log", [])[mark:]
        return {
            "llm_calls": len(entries),
            "failed_llm_calls": sum(bool(e.get("failed")) for e in entries),
            "prompt_tokens": sum(e["prompt_tokens"] for e in entries),
            "completion_tokens": sum(e["completion_tokens"] for e in entries),
            "total_tokens": sum(e["total_tokens"] for e in entries),
            "cached_tokens": sum(e["cached_tokens"] for e in entries),
            "latency_ms": round(sum(e["latency_ms"] for e in entries), 1),
        }
