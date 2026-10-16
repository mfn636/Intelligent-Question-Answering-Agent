"""真实 Runner/本地工具的 Web 集成测试；模型使用替身，不访问外部服务。"""

import json
from threading import Event
import time
from types import SimpleNamespace
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from bootstrap import create_application
from web.app import COOKIE_NAME, create_web_app


def reply(content="测试回答", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


class ScriptedLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.usage_log = []
        self.requests = []

    def chat(self, **kwargs):
        self.requests.append(kwargs)
        if not self.responses:
            raise AssertionError("测试不应发起额外模型请求")
        response = self.responses.pop(0)
        if callable(response):
            response = response()
        if isinstance(response, Exception):
            raise response
        self.usage_log.append({
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
            "cached_tokens": 0, "latency_ms": 1,
        })
        return response


class WebTests(unittest.TestCase):
    def client(self, llm=None):
        llm = llm or ScriptedLLM()
        app = create_web_app(lambda: create_application(llm=llm))
        context = TestClient(app)
        client = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return client

    def session(self, client):
        response = client.post("/api/sessions")
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def send(self, client, session_id, content, request_id=None):
        return client.post(f"/api/sessions/{session_id}/messages", json={
            "content": content, "request_id": request_id or uuid4().hex,
        })

    def finished(self, client, session_id):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            snapshot = client.get(f"/api/sessions/{session_id}").json()
            if snapshot["phase"] not in ("queued", "running"):
                return snapshot
            time.sleep(0.01)
        self.fail("后台请求未在测试期限内结束")

    def test_static_page_and_browser_isolation(self):
        client = self.client()
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("记忆与状态", page.text)
        self.assertIn("script-src 'self'", page.headers["content-security-policy"])
        self.assertEqual(client.get("/static/app.js").status_code, 200)
        self.assertEqual(client.get("/static/styles.css").status_code, 200)
        first = self.session(client)
        first_cookie = client.cookies.get(COOKIE_NAME)
        client.cookies.clear()
        self.assertEqual(client.get("/api/sessions").json()["sessions"], [])
        self.assertEqual(client.get(f"/api/sessions/{first}").status_code, 404)
        self.assertEqual(self.send(client, first, "你好").status_code, 404)
        self.assertEqual(client.delete(f"/api/sessions/{first}").status_code, 404)
        self.session(client)
        client.cookies.clear()
        client.cookies.set(COOKIE_NAME, first_cookie)
        self.assertEqual([s["id"] for s in client.get("/api/sessions").json()["sessions"]], [first])
        self.assertEqual(client.get(f"/api/sessions/{first}").headers["cache-control"], "no-store")

    def test_workflow_exposes_real_memory_state_and_tool_result(self):
        client = self.client()
        first, second = self.session(client), self.session(client)
        self.assertEqual(self.send(client, first, "NB001 有货吗").status_code, 202)
        snapshot = self.finished(client, first)
        self.assertEqual(snapshot["phase"], "completed")
        self.assertEqual(snapshot["state"]["status"], "completed")
        self.assertEqual(snapshot["state"]["trace"]["route"]["kind"], "workflow")
        event = snapshot["state"]["trace"]["tool_events"][0]
        self.assertEqual(event["intent"]["tool"], "check_inventory")
        self.assertEqual(json.loads(event["result"])[0]["product_id"], "NB001")
        self.assertEqual([m["role"] for m in snapshot["memory"]["messages"]], ["user", "assistant"])
        self.assertIn("tool", [m["role"] for m in snapshot["state"]["messages"]])
        self.assertEqual(snapshot["usage"]["llm_calls"], 0)
        self.assertEqual(len(snapshot["messages"]), 2)
        self.assertEqual(client.get(f"/api/sessions/{second}").json()["memory"]["messages"], [])
        self.assertEqual(client.delete(f"/api/sessions/{first}").status_code, 204)
        self.assertEqual(client.get(f"/api/sessions/{first}").status_code, 404)

    def test_react_tool_round_trip(self):
        call = SimpleNamespace(id="test-call", type="function", function=SimpleNamespace(
            name="get_product_detail", arguments=json.dumps({"product_id": "NB001"}),
        ))
        client = self.client(ScriptedLLM(reply(None, [call]), reply("已查询商品，可以结合预算选择。")))
        sid = self.session(client)
        self.send(client, sid, "推荐一台电脑")
        snapshot = self.finished(client, sid)
        self.assertEqual(snapshot["state"]["step"], 2)
        self.assertEqual(snapshot["state"]["trace"]["route"]["kind"], "agent")
        self.assertEqual(len(snapshot["state"]["trace"]["tool_events"]), 1)
        self.assertEqual(snapshot["usage"]["llm_calls"], 2)
        self.assertEqual(snapshot["messages"][-1]["content"], "已查询商品，可以结合预算选择。")

    def test_running_snapshot_duplicate_requests_and_serialized_turns(self):
        entered, release = Event(), Event()

        def blocking_reply():
            entered.set()
            if not release.wait(4):
                raise TimeoutError("test release timed out")
            return reply("推荐回答")

        client = self.client(ScriptedLLM(blocking_reply))
        first, second = self.session(client), self.session(client)
        request_id = uuid4().hex
        try:
            self.send(client, first, "推荐电脑", request_id)
            self.assertTrue(entered.wait(2))
            self.assertEqual(client.get("/api/health").status_code, 200)
            snapshot = client.get(f"/api/sessions/{first}").json()
            self.assertEqual(snapshot["state"]["status"], "running")
            self.assertEqual(snapshot["state"]["step"], 1)
            self.assertEqual(self.send(client, first, "推荐电脑", request_id).status_code, 202)
            self.assertEqual(self.send(client, first, "你好").status_code, 409)
            self.assertEqual(self.send(client, first, "另一个问题", request_id).status_code, 409)
            self.assertEqual(client.delete(f"/api/sessions/{first}").status_code, 409)
            self.send(client, second, "你好")
            self.assertEqual(client.get(f"/api/sessions/{second}").json()["phase"], "queued")
        finally:
            release.set()
        self.assertEqual(len(self.finished(client, first)["messages"]), 2)
        self.assertEqual(self.finished(client, second)["usage"]["llm_calls"], 0)
        # 响应丢失后重发已完成的请求也不会重复执行。
        self.send(client, first, "推荐电脑", request_id)
        self.assertEqual(len(client.get(f"/api/sessions/{first}").json()["messages"]), 2)

    def test_compaction_keeps_display_transcript(self):
        llm = ScriptedLLM(reply("此前进行了多次问候。"), reply("推荐回答"))
        client = self.client(llm)
        sid = self.session(client)
        for _ in range(11):
            self.send(client, sid, "你好")
            self.finished(client, sid)
        self.send(client, sid, "推荐电脑")
        snapshot = self.finished(client, sid)
        self.assertEqual(snapshot["memory"]["summary"], "此前进行了多次问候。")
        self.assertLessEqual(len(snapshot["memory"]["messages"]), 10)
        self.assertEqual(len(snapshot["messages"]), 24)

    def test_failure_is_visible_without_leaking_exception_details(self):
        client = self.client(ScriptedLLM(RuntimeError("private API credentials")))
        sid = self.session(client)
        with self.assertLogs("web.sessions", level="ERROR"):
            self.send(client, sid, "推荐电脑")
            snapshot = self.finished(client, sid)
        self.assertEqual(snapshot["phase"], "failed")
        self.assertEqual(snapshot["state"]["status"], "failed")
        self.assertEqual(snapshot["messages"][-1]["role"], "error")
        self.assertNotIn("private API credentials", json.dumps(snapshot))
        self.send(client, sid, "你好")
        self.assertEqual(self.finished(client, sid)["phase"], "completed")

    def test_input_validation_does_not_start_a_turn(self):
        client = self.client()
        sid = self.session(client)
        for content in ("", "  \n ", "a" * 10001, 12, None):
            self.assertEqual(self.send(client, sid, content).status_code, 422)
        self.assertEqual(client.get(f"/api/sessions/{sid}").json()["messages"], [])

    def test_application_lifecycle(self):
        application = create_application(llm=ScriptedLLM())
        with TestClient(create_web_app(lambda: application)) as client:
            self.assertFalse(application._closed)
            self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
        self.assertTrue(application._closed)


if __name__ == "__main__":
    unittest.main()
