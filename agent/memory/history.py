"""
agent/memory/history.py

内存中的会话记忆：近期 user/assistant 原文与较早对话摘要，不读写文件。

"""



class ConversationHistory:

    def __init__(self):
        self.messages = []
        self.summary = ""

    def add_user(self, content):
        """追加一条 user 消息。"""
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content):
        """追加一条 assistant 消息。"""
        self.messages.append({"role": "assistant", "content": content})

    def compact(self, summarizer, *, keep: int, trigger: int) -> bool:
        """超过阈值时压缩完整问答；新摘要成功生成后才释放对应原文。"""
        if not 1 <= keep <= trigger:
            raise ValueError("记忆窗口必须满足 1 <= keep <= trigger")
        if len(self.messages) <= trigger:
            return False

        # 边界落在 assistant 回答之后、下一条 user 之前，保留当前待答问题。
        start = max(1, len(self.messages) - keep)
        boundary = next((
            end for end in range(start, len(self.messages))
            if self.messages[end - 1]["role"] == "assistant"
            and self.messages[end]["role"] == "user"
        ), None)
        if boundary is None:
            return False

        # 传入副本，摘要器失败或改写输入都不会破坏原始记录。
        outgoing = [dict(message) for message in self.messages[:boundary]]
        summary = summarizer.summarize(self.summary, outgoing)
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("摘要不能为空")

        self.summary = summary.strip()
        del self.messages[:boundary]
        return True
