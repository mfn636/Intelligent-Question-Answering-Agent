"""路由与固定流程的行为测试；不访问模型 API 或 RAG。"""

import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import bootstrap
from agent.core.types import RouteDecision
from agent.core.state import AgentState
from llm.client import LLMClient, LazyLLMClient
from routing.intent_router import IntentRouter
from tool_providers.local import LocalProvider
from workflows.product_queries import ProductQueryWorkflows


def response(content):
    return SimpleNamespace(content=content, tool_calls=[])


def candidate(intent, product_id=None, source="none"):
    return response(json.dumps({"intent": intent, "product_id": product_id, "source": source}))


class ScriptedLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.usage_log = []
        self.requests = []

    def chat(self, **kwargs):
        self.requests.append(kwargs)
        if not self.responses:
            raise AssertionError("不应额外调用模型")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        self.usage_log.append({
            "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
            "cached_tokens": 0, "latency_ms": 1,
        })
        return result


class RoutingTests(unittest.TestCase):
    def test_exact_shortcuts_need_no_model(self):
        llm = ScriptedLLM()
        router = IntentRouter(llm=llm)
        cases = [
            ("你好！", "direct", "greeting"),
            ("谢谢", "direct", "thanks"),
            ("", "direct", "no_actionable_request"),
            ("你好，NB001 多少钱？", "workflow", "price_lookup"),
            ("请帮我查询 NB001 库存", "workflow", "inventory_lookup"),
            ("NB001 的参数", "workflow", "product_detail"),
            ("查库存", "direct", "inventory_lookup"),
        ]
        for text, kind, intent in cases:
            with self.subTest(text=text):
                decision = router.route(text, [])
                self.assertEqual((decision.kind, decision.intent), (kind, intent))
        self.assertEqual(llm.requests, [])

    def test_complex_goals_do_not_get_truncated_or_classified(self):
        llm = ScriptedLLM()
        router = IntentRouter(llm=llm)
        for text in (
            "NB001 有货吗，多少钱", "你好，比较 NB001 和 NB002", "如果 NB001 有货就推荐配件",
            "推荐预算 3000 的耳机，排除某品牌", "NB001 的 CPU 和内存是什么",
            "NB001 不要了，查 NB002 库存", "查商品和库存", "我要退货", "继续", "就它吧",
        ):
            with self.subTest(text=text):
                self.assertEqual(router.route(text, []).kind, "agent")
        self.assertEqual(llm.requests, [])

    def test_classifier_keeps_business_request_despite_negative_tone(self):
        llm = ScriptedLLM(candidate("inventory_lookup", "NB001", "current_input"))
        decision = IntentRouter(llm=llm).route("你们太差了，NB001 到底有没有货", [])
        self.assertEqual(decision.kind, "workflow")
        self.assertEqual(decision.params, {"product_id": "NB001"})
        self.assertEqual(len(llm.requests), 1)
        self.assertIsNone(llm.requests[0]["tools"])
        self.assertFalse(llm.requests[0]["thinking"])

    def test_invalid_or_invented_classifier_results_fall_back(self):
        examples = [
            response("not json"),
            response('{"intent":"delete_database","product_id":null,"source":"none"}'),
            response('{"intent":"inventory_lookup","product_id":1,"source":"current_input"}'),
            response('{"intent":"inventory_lookup","product_id":"NB001","source":"current_input","extra":1}'),
            candidate("inventory_lookup", "NB999", "current_input"),
            candidate("inventory_lookup", "NB001", "history"),
            candidate("inventory_lookup"),
            candidate("price_lookup", "NB001", "current_input"),
            candidate("out_of_scope"),
            SimpleNamespace(content=None, tool_calls=[object()]),
            RuntimeError("classifier unavailable"),
        ]
        for answer in examples:
            with self.subTest(answer=answer):
                llm = ScriptedLLM(answer)
                result = IntentRouter(llm=llm).route("麻烦确认一下 NB001 到底有没有货", [])
                self.assertEqual(result.kind, "agent")
                self.assertEqual(len(llm.requests), 1)

    def test_history_reference_requires_one_unambiguous_id(self):
        router = IntentRouter(llm=ScriptedLLM())
        single = [{"role": "assistant", "content": "NB001 参数已查询"}]
        multiple = [{"role": "assistant", "content": "NB001 和 NB002 都可以"}]
        self.assertEqual(router.route("它有货吗", single).params, {"product_id": "NB001"})
        self.assertEqual(router.route("它有货吗", multiple).kind, "direct")
        self.assertEqual(router.route("NB003 有货吗", multiple).params, {"product_id": "NB003"})
        old = single + [{"role": "assistant", "content": "聊天"}] * 10
        self.assertEqual(router.route("它有货吗", old).kind, "direct")
        cancelled = [{"role": "user", "content": "不要 NB001 了"}]
        self.assertEqual(router.route("查库存", cancelled).kind, "direct")

    def test_classifier_can_select_out_of_scope_direct_reply(self):
        llm = ScriptedLLM(candidate("out_of_scope"))
        decision = IntentRouter(llm=llm).route("帮我写一首古诗", [])
        self.assertEqual(decision.kind, "direct")
        self.assertEqual(decision.intent, "out_of_scope")
        self.assertEqual(len(llm.requests), 1)

    def test_classifier_cannot_borrow_an_id_for_a_new_named_product(self):
        llm = ScriptedLLM(candidate("price_lookup", "NB001", "history"))
        history = [{"role": "assistant", "content": "NB001"}]
        result = IntentRouter(llm=llm).route("Apex 新款笔记本卖多少钱呀", history)
        self.assertEqual(result.kind, "agent")

    def test_classifier_receives_current_query_once_and_recent_history_only(self):
        llm = ScriptedLLM(candidate("unknown"))
        history = [{"role": "user", "content": str(i)} for i in range(20)]
        IntentRouter(llm=llm).route("聊个事情", history)
        payload = json.loads(llm.requests[0]["messages"][1]["content"])
        self.assertEqual(payload, {"user_input": "聊个事情", "history": history[-10:]})


class WorkflowTests(unittest.TestCase):
    def execute_inventory(self, result):
        provider = Mock()
        if isinstance(result, Exception):
            provider.call.side_effect = result
        else:
            provider.call.return_value = result
        workflows = ProductQueryWorkflows(tools=provider)
        state = AgentState()
        decision = RouteDecision(kind="workflow", intent="inventory_lookup", params={"product_id": "NB001"})
        reply = workflows.execute(decision, state)
        provider.call.assert_called_once_with("check_inventory", {"product_id": "NB001"})
        self.assertEqual(state.step, 0)
        self.assertEqual(len(state.trace.tool_events), 1)
        request, result_message = state.messages
        self.assertEqual(request["tool_calls"][0]["id"], result_message["tool_call_id"])
        return reply, state

    def test_zero_stock_empty_results_and_execution_failure_are_distinct(self):
        zero = json.dumps([{"product_id": "NB001", "product_name": "电脑", "stock": 0, "in_stock": False}])
        reply, state = self.execute_inventory(zero)
        self.assertIn("0 件", reply)
        self.assertNotIn("error", state.trace.tool_events[0])
        self.assertIn("未查到", self.execute_inventory("[]")[0])
        self.assertIn("查询失败", self.execute_inventory(TimeoutError("offline"))[0])

    def test_bad_results_fail_without_guessing_or_retrying(self):
        valid = {"product_id": "NB001", "product_name": "电脑", "stock": 1, "in_stock": True}
        bad_results = [None, "bad json", "{}", "null", "[NaN]", "[1]", json.dumps([valid, valid])]
        for updates in (
            {"product_id": "NB002"}, {"stock": True}, {"stock": -1},
            {"stock": "1"}, {"in_stock": False}, {"product_name": ""},
        ):
            bad_results.append(json.dumps([{**valid, **updates}]))
        for result in bad_results:
            with self.subTest(result=result):
                reply, state = self.execute_inventory(result)
                self.assertIn("查询失败", reply)
                self.assertIn("error", state.trace.tool_events[0])

    def test_workflow_refuses_missing_id_before_execution(self):
        provider = Mock()
        workflows = ProductQueryWorkflows(tools=provider)
        with self.assertRaises(ValueError):
            workflows.execute(RouteDecision(kind="workflow", intent="inventory_lookup"), AgentState())
        provider.call.assert_not_called()

    def test_price_and_detail_validate_the_fields_they_use(self):
        provider = Mock()
        workflows = ProductQueryWorkflows(tools=provider)
        for price in (None, True, "123", -1, float("inf")):
            with self.subTest(price=price):
                provider.call.return_value = json.dumps([{"id": "NB001", "name": "电脑", "price": price}])
                reply = workflows.execute(RouteDecision(kind="workflow", intent="price_lookup", params={"product_id": "NB001"}), AgentState())
                self.assertIn("查询失败", reply)
        provider.call.return_value = '[{"id":"NB001","name":"电脑","cpu":null}]'
        reply = workflows.execute(RouteDecision(kind="workflow", intent="product_detail", params={"product_id": "NB001"}), AgentState())
        self.assertIn("暂无已提供", reply)
        self.assertNotIn("处理器：", reply)


class ConversationRoutingTests(unittest.TestCase):
    def test_classified_workflow_uses_one_model_call_and_one_tool_call(self):
        llm = ScriptedLLM(candidate("inventory_lookup", "NB001", "current_input"))
        with bootstrap.create_application(llm=llm) as app:
            runner = app.create_conversation()
            with patch.object(app.tools, "call", wraps=app.tools.call) as call:
                result = runner.run("你们太差了，NB001 到底有没有货")
            self.assertEqual(result.usage["llm_calls"], 1)
            self.assertEqual(result.usage["total_tokens"], 12)
            call.assert_called_once_with("check_inventory", {"product_id": "NB001"})
            self.assertEqual(runner.last_trace.route["source"], "classifier")
            self.assertEqual(runner.state.step, 0)

    def test_failed_classifier_request_is_counted_by_actual_llm_adapter(self):
        sdk = Mock()
        sdk.chat.completions.create.side_effect = [
            TimeoutError("classifier unavailable"),
            SimpleNamespace(choices=[SimpleNamespace(message=response("正常回答"))], usage=None),
        ]
        llm = LLMClient(sdk)
        with bootstrap.create_application(llm=llm) as app:
            result = app.create_conversation().run("聊一个事情")
        self.assertEqual(result.usage["llm_calls"], 2)
        self.assertEqual(result.usage["failed_llm_calls"], 1)

    def test_default_lazy_sdk_is_closed_when_application_exits(self):
        sdk = Mock()
        sdk.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=response("推荐回答"))], usage=None,
        )
        with patch("dotenv.load_dotenv"), patch("bootstrap.os.getenv", return_value="test-value"), patch(
            "openai.OpenAI", return_value=sdk
        ) as constructor:
            with bootstrap.create_application() as app:
                constructor.assert_not_called()
                app.create_conversation().run("推荐电脑")
                constructor.assert_called_once()
                sdk.close.assert_not_called()
            sdk.close.assert_called_once()

    def test_real_local_tools_and_clarification_followup(self):
        llm = ScriptedLLM()
        with bootstrap.create_application(llm=llm) as app:
            runner = app.create_conversation()
            for query, expected in (
                ("你好", "你好"), ("查库存", "请提供"), ("NB001", "库存"),
                ("它多少钱", "6299"), ("NB001 参数", "Zenon"), ("谢谢", "不客气"),
            ):
                with self.subTest(query=query):
                    result = runner.run(query)
                    self.assertIn(expected, result.replies[0])
                    self.assertEqual(result.usage["llm_calls"], 0)
                    self.assertIn("elapsed_ms", result.usage)
                    self.assertEqual(runner.state.step, 0)
                    self.assertEqual(runner.state.status, "completed")
            self.assertEqual(len(runner.history.messages), 12)
            self.assertEqual(runner.last_trace.tool_events, [])
            self.assertEqual(llm.requests, [])

    def test_classifier_cost_and_agent_cost_both_counted(self):
        llm = ScriptedLLM(candidate("unknown"), response("已处理"))
        with bootstrap.create_application(llm=llm) as app:
            runner = app.create_conversation()
            result = runner.run("帮我看看这个事情")
            self.assertEqual(result.usage["llm_calls"], 2)
            self.assertEqual(result.usage["total_tokens"], 24)
            self.assertEqual(runner.state.step, 1)
            self.assertEqual(runner.last_trace.route["kind"], "agent")
            self.assertEqual(len(runner.history.messages), 2)

    def test_classifier_failure_preserves_original_request_for_agent(self):
        llm = ScriptedLLM(TimeoutError("classifier unavailable"), response("正常回答"))
        with bootstrap.create_application(llm=llm) as app:
            runner = app.create_conversation()
            result = runner.run("帮我看看这个事情")
            self.assertEqual(result.replies, ["正常回答"])
            self.assertEqual(runner.last_trace.route["reason"], "classifier_failed")
            self.assertEqual(llm.requests[-1]["messages"][1], {"role": "user", "content": "帮我看看这个事情"})

    def test_route_switches_keep_history_and_trace_separate(self):
        llm = ScriptedLLM(response("建议根据用途选择"))
        with bootstrap.create_application(llm=llm) as app:
            runner = app.create_conversation()
            decisions = []
            traces = []
            for query in ("你好", "NB001 有货吗", "推荐一台电脑", "谢谢"):
                result = runner.run(query)
                decisions.append(runner.last_trace.route["kind"])
                traces.append(runner.last_trace)
            self.assertEqual(decisions, ["direct", "workflow", "agent", "direct"])
            self.assertEqual([len(t.tool_events) for t in traces], [0, 1, 0, 0])
            self.assertEqual(len(runner.history.messages), 8)
            self.assertEqual(result.usage["llm_calls"], 0)
            self.assertEqual(len(llm.requests), 1)

    def test_fixed_path_failure_is_final_for_this_turn(self):
        llm = ScriptedLLM()
        with bootstrap.create_application(llm=llm) as app:
            runner = app.create_conversation()
            with patch.object(app.tools, "call", side_effect=TimeoutError("offline")) as call:
                result = runner.run("NB001 有货吗")
            call.assert_called_once()
            self.assertIn("查询失败", result.replies[0])
            self.assertEqual(llm.requests, [])
            self.assertIn("error", runner.last_trace.tool_events[0])

    def test_empty_provider_falls_back_before_executing_workflow(self):
        llm = ScriptedLLM(response("暂时没有可用工具"))
        tools = LocalProvider(tool_schemas=[], tool_lookup={})
        with bootstrap.create_application(llm=llm, tools=tools) as app:
            runner = app.create_conversation()
            runner.run("NB001 有货吗")
            self.assertEqual(runner.last_trace.route["reason"], "workflow_unavailable")
            self.assertEqual(runner.last_trace.tool_events, [])

    def test_long_fixed_path_conversation_does_not_call_summary_model(self):
        llm = ScriptedLLM()
        with bootstrap.create_application(llm=llm) as app:
            runner = app.create_conversation()
            for _ in range(12):
                runner.run("你好")
            self.assertEqual(llm.requests, [])
            self.assertEqual(len(runner.history.messages), 24)
            # 下一次进入 Agent 才做摘要，且不丢掉待答问题。
            llm.responses.extend([response("之前双方打过招呼。"), response("推荐回答")])
            result = runner.run("推荐电脑")
            self.assertEqual(result.usage["llm_calls"], 2)
            self.assertEqual(runner.history.summary, "之前双方打过招呼。")
            self.assertEqual(runner.history.messages[-2]["content"], "推荐电脑")

    def test_default_shortcuts_do_not_load_model_config_even_after_summary_threshold(self):
        with patch("dotenv.load_dotenv", side_effect=AssertionError("不应读取配置")), patch(
            "openai.OpenAI", side_effect=AssertionError("不应创建客户端")
        ):
            with bootstrap.create_application() as app:
                runner = app.create_conversation()
                for _ in range(12):
                    runner.run("你好")
                result = runner.run("你好，NB001 多少钱")
                self.assertIn("6299", result.replies[0])
                self.assertEqual(result.usage["llm_calls"], 0)

    def test_lazy_llm_initializes_once_and_cannot_initialize_after_close(self):
        concrete = ScriptedLLM(response("a"), response("b"))
        factory = Mock(return_value=concrete)
        llm = LazyLLMClient(factory)
        self.assertEqual(llm.usage_log, [])
        factory.assert_not_called()
        llm.chat(messages=[])
        llm.chat(messages=[])
        factory.assert_called_once_with()
        self.assertEqual(len(llm.usage_log), 2)
        unopened = LazyLLMClient(factory)
        unopened.close()
        with self.assertRaises(RuntimeError):
            unopened.chat(messages=[])
        factory.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
