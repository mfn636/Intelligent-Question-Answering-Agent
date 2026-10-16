"""Web 会话与后台执行；页面记录独立于会被摘要压缩的 Agent 记忆。"""

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from uuid import uuid4

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from agent.core.runner import ConversationRunner


logger = logging.getLogger(__name__)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def message(role: str, content: str) -> dict:
    return {"id": uuid4().hex, "role": role, "content": content, "created_at": now()}


@dataclass
class WebSession:
    id: str
    owner: str
    runner: ConversationRunner
    title: str = "新的对话"
    updated_at: str = field(default_factory=now)
    messages: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    phase: str = "idle"
    error: str | None = None
    task: asyncio.Task | None = None
    requests: dict[str, str] = field(default_factory=dict)

    def brief(self) -> dict:
        return {
            "id": self.id, "title": self.title, "updated_at": self.updated_at,
            "phase": self.phase, "message_count": len(self.messages),
        }

    def snapshot(self) -> dict:
        # 核心执行器只会追加消息/事件或替换字段。先取列表/字典副本，
        # 再序列化；观察接口不修改 Runner，也不向它的上下文插入 UI 消息。
        state = self.runner.state
        history = self.runner.history
        return {
            **self.brief(),
            "messages": deepcopy(self.messages),
            "usage": dict(self.usage),
            "error": self.error,
            "memory": {
                "summary": history.summary,
                "messages": deepcopy(list(history.messages)),
                "window": self.runner.WINDOW,
                "summary_trigger": self.runner.SUMMARY_TRIGGER,
            },
            "state": {
                "user_input": state.user_input,
                "messages": deepcopy(list(state.messages)),
                "step": state.step,
                "status": state.status,
                "final_replies": list(state.final_replies),
                "trace": {
                    "route": deepcopy(dict(state.trace.route)),
                    "tool_events": deepcopy(list(state.trace.tool_events)),
                },
            },
        }


class SessionStore:
    MAX_SESSIONS_PER_BROWSER = 50

    def __init__(self, application):
        self.application = application
        self.sessions: dict[str, WebSession] = {}
        self._tasks: set[asyncio.Task] = set()
        self._closing = False
        # 当前组合根共享 LazyLLMClient / usage_log / RAG 资源。将整轮执行
        # 串行化，避免并发初始化和不同会话的用量串账；HTTP 读请求仍可并发。
        self._execution_lock = asyncio.Lock()

    def create(self, owner: str) -> WebSession:
        if len(self.list(owner)) >= self.MAX_SESSIONS_PER_BROWSER:
            raise HTTPException(409, "会话数量已达上限，请先删除不需要的会话。")
        session_id = uuid4().hex
        entry = WebSession(
            id=session_id, owner=owner,
            runner=self.application.create_conversation(session_id=session_id),
        )
        self.sessions[session_id] = entry
        return entry

    def list(self, owner: str) -> list[dict]:
        entries = [entry.brief() for entry in self.sessions.values() if entry.owner == owner]
        return sorted(entries, key=lambda item: item["updated_at"], reverse=True)

    def get(self, owner: str, session_id: str) -> WebSession:
        entry = self.sessions.get(session_id)
        if entry is None or entry.owner != owner:
            raise HTTPException(404, "会话不存在或服务已重启，请新建对话。")
        return entry

    def delete(self, owner: str, session_id: str):
        entry = self.get(owner, session_id)
        if entry.phase in ("queued", "running"):
            raise HTTPException(409, "对话正在执行，请等待完成后再删除。")
        del self.sessions[session_id]

    def submit(self, entry: WebSession, content: str, request_id: str):
        if self._closing:
            raise HTTPException(503, "服务正在关闭，请稍后重试。")
        # 网络重试沿用 request_id，避免同一条消息被执行两遍。
        if request_id in entry.requests:
            if entry.requests[request_id] != content:
                raise HTTPException(409, "请求编号已用于另一条消息。")
            return
        if entry.phase in ("queued", "running"):
            raise HTTPException(409, "当前会话正在回答，请等待完成。")
        entry.requests[request_id] = content
        if not entry.messages:
            entry.title = content[:28] + ("…" if len(content) > 28 else "")
        entry.messages.append(message("user", content))
        entry.phase = "queued"
        entry.error = None
        entry.usage = {}
        entry.updated_at = now()
        entry.task = asyncio.create_task(self._run(entry, content))
        self._tasks.add(entry.task)
        entry.task.add_done_callback(self._tasks.discard)

    async def _run(self, entry: WebSession, content: str):
        async with self._execution_lock:
            entry.phase = "running"
            try:
                result = await run_in_threadpool(entry.runner.run, content)
                entry.messages.extend(message("assistant", reply) for reply in result.replies)
                entry.usage = dict(result.usage)
                entry.phase = "completed"
            except Exception as exc:
                logger.exception("Web conversation failed: %s", entry.id)
                entry.phase = "failed"
                entry.error = (
                    "尚未配置模型密钥，请在项目 .env 中设置 DEEPSEEK_API_KEY，然后重新发送。"
                    if isinstance(exc, ValueError) and "未找到 DEEPSEEK_API_KEY" in str(exc)
                    else "这次请求未能完成，请稍后重试。详细错误已记录在服务端日志。"
                )
                entry.messages.append(message("error", entry.error))
            finally:
                entry.updated_at = now()

    async def close(self):
        self._closing = True
        # 等已接受的请求结束后才关闭共享模型/检索资源。
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
