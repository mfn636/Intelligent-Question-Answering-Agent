"""启动：python -m uvicorn web.app:app --host 127.0.0.1 --port 8000。"""

from contextlib import asynccontextmanager
from pathlib import Path
import re
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from bootstrap import create_application
from web.sessions import SessionStore


STATIC_DIR = Path(__file__).resolve().parent / "static"
COOKIE_NAME = "agent_workspace"


class MessageRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=10000)
    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


def create_web_app(application_factory=create_application) -> FastAPI:
    """导入不加载资源；lifespan 创建/关闭 Application，测试可注入替身。"""

    @asynccontextmanager
    async def lifespan(app):
        application = await run_in_threadpool(application_factory)
        store = SessionStore(application)
        app.state.sessions = store
        try:
            yield
        finally:
            await store.close()
            await run_in_threadpool(application.close)

    app = FastAPI(title="Agent Workspace", lifespan=lifespan)

    @app.middleware("http")
    async def browser_scope(request: Request, call_next):
        owner = request.cookies.get(COOKIE_NAME, "")
        new_owner = re.fullmatch(r"[a-f0-9]{64}", owner) is None
        if new_owner:
            owner = secrets.token_hex(32)
        request.state.owner = owner
        response = await call_next(request)
        if new_owner:
            response.set_cookie(
                COOKIE_NAME, owner, httponly=True, samesite="strict",
                secure=request.url.scheme == "https",
            )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        # FastAPI 自带的文档页使用 CDN 和内联初始化脚本；工作台本身只加载本地资源。
        if request.url.path not in {"/docs", "/redoc", "/docs/oauth2-redirect"}:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'self'; form-action 'self'"
            )
        return response

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/sessions")
    async def list_sessions(request: Request):
        return {"sessions": request.app.state.sessions.list(request.state.owner)}

    @app.post("/api/sessions", status_code=201)
    async def create_session(request: Request):
        return request.app.state.sessions.create(request.state.owner).snapshot()

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, request: Request):
        return request.app.state.sessions.get(request.state.owner, session_id).snapshot()

    @app.post("/api/sessions/{session_id}/messages", status_code=202)
    async def send_message(session_id: str, body: MessageRequest, request: Request):
        store = request.app.state.sessions
        entry = store.get(request.state.owner, session_id)
        store.submit(entry, body.content, body.request_id)
        return entry.snapshot()

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str, request: Request):
        request.app.state.sessions.delete(request.state.owner, session_id)
        return Response(status_code=204)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_web_app()
