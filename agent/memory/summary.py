"""用注入的模型生成会话摘要；不持有会话状态，不读写文件。"""

import json

from prompts.memory import SUMMARY_SYSTEM_PROMPT


class ConversationSummarizer:
    MAX_SUMMARY_CHARS = 2000

    def __init__(self, *, llm):
        self.llm = llm

    def summarize(self, previous_summary: str, messages: list[dict]) -> str:
        response = self.llm.chat(
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({
                    "previous_summary": previous_summary,
                    "messages": messages,
                }, ensure_ascii=False)},
            ],
            tools=None,
            temperature=0.0,
            max_tokens=1800,
            thinking=False,
        )
        if getattr(response, "tool_calls", None):
            raise ValueError("摘要响应不能包含工具调用")
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("摘要响应为空或不是文本")
        summary = content.strip()
        if len(summary) > self.MAX_SUMMARY_CHARS:
            raise ValueError("摘要超过长度上限，保留原文以免截断重要信息")
        return summary
