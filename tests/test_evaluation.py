"""Evaluation integrity tests: no live API, no Qdrant, no model-scored assertions."""
from collections import Counter
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from eval.common import BudgetExceeded, ROOT, read_json, read_jsonl, supervised
from eval.recorders import ObservedLLM, Recorder, argument_check
from eval.report import aggregate, cost, paired, validate_prices
from eval.runtime import agent_worker, assemble
from eval.schema import load_cases
from eval.scoring import review_template, score, usage_totals, validate_review
from eval.run_memory import score_fields
from eval.run_agent import main as run_agent
from eval.run_retrieval import ranking_metrics, valid_scores


def message(content=None, tool=None, args=None):
    calls = [SimpleNamespace(id="tc-1", type="function", function=SimpleNamespace(name=tool, arguments=args))] if tool else []
    return SimpleNamespace(content=content, tool_calls=calls)


class FakeLLM:
    def __init__(self, responses):
        self.responses, self.usage_log, self.requests = iter(responses), [], []

    def chat(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            self.usage_log.append({"failed": True, "model": "test", "prompt_tokens": 0, "completion_tokens": 0,
                                   "cached_tokens": 0, "total_tokens": 0, "latency_ms": 10})
            raise response
        self.usage_log.append({"model": "test", "prompt_tokens": 20, "completion_tokens": 5,
                               "cached_tokens": 3, "total_tokens": 25, "latency_ms": 1})
        return response


def slow_worker(payload, directory):
    recorder = Recorder(directory)
    recorder.turn = 1
    recorder.emit("turn_start")
    recorder.emit("llm_start", call_id=1, stage="executor")
    time.sleep(5)
    (Path(directory) / "escaped.txt").write_text("should never finish", encoding="utf-8")


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def test_draft_dataset_counts_groups_and_long_scenarios(self):
        cases = load_cases(ROOT / "data/eval/agent_v1.jsonl")
        self.assertEqual(Counter(c["category"] for c in cases), {"social": 10, "single_product": 20, "context": 20,
                                                               "multi_tool": 15, "knowledge": 20, "fault": 15})
        self.assertEqual(Counter(c["split"] for c in cases), {"dev": 60, "acceptance": 40})
        self.assertEqual(sum(c["pilot"] for c in cases), 30)
        self.assertTrue(all(c["split"] == "dev" for c in cases if c["pilot"]))
        self.assertEqual(sum("long" in c["tags"] for c in cases), 8)
        self.assertTrue(all(len(c["turns"]) >= 18 for c in cases if "long" in c["tags"]))
        self.assertTrue(all(c["annotation_status"] == "draft_needs_human_review" for c in cases))

    def test_workflow_calls_do_not_count_as_model_arguments(self):
        recorder = Recorder(self.path)
        with ExitStack() as resources:
            runner, _, _ = assemble(resources, recorder, variant="routed", rag_available=False, llm=FakeLLM([]))
            recorder.turn = 1
            reply = runner.run("NB002 有货吗")
        self.assertIn("0", reply.replies[0])
        events = read_jsonl(recorder.path)
        self.assertFalse(any(e["kind"] == "model_tool_request" for e in events))
        calls = [e for e in events if e["kind"] == "tool_end"]
        self.assertEqual(calls[0]["source"], "workflow")
        self.assertTrue(calls[0]["function_entered"])

    def test_manifest_accepts_checkout_line_endings_but_rejects_data_edits(self):
        manifest = read_json(ROOT / "data/eval/manifest.json")
        paths = ["data/eval/agent_v1.jsonl", "data/eval/manifest.json", *manifest["sources"]]
        for newline in (b"\n", b"\r\n"):
            for name in paths:
                target = self.path / name
                target.parent.mkdir(parents=True, exist_ok=True)
                original = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
                target.write_bytes(original.replace(b"\n", newline))
            with patch("eval.run_agent.ROOT", self.path), redirect_stdout(StringIO()):
                run_agent(["--validate-only", "--suite", "all"])
        for name in ("data/products.json", "data/eval/agent_v1.jsonl"):
            target = self.path / name
            original = target.read_bytes()
            target.write_bytes(original.replace(b"NB001", b"NB999", 1))
            with patch("eval.run_agent.ROOT", self.path), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as error:
                    run_agent(["--validate-only", "--suite", "all"])
                self.assertEqual(error.exception.code, 2)
            target.write_bytes(original)

    def test_scoring_labels_never_enter_model_context(self):
        llm = FakeLLM([message("你好")])
        case = deepcopy(load_cases(ROOT / "data/eval/agent_v1.jsonl")[0])
        case["turns"][0]["checks"][0]["requirement"] = "SECRET_EVAL_EXPECTATION_DO_NOT_SEND"
        with patch("bootstrap._build_llm", return_value=llm):
            agent_worker({"case": case, "variant": "react", "rag_available": False,
                          "max_calls": 3, "max_tokens": 1000}, self.path)
        self.assertTrue(llm.requests)
        self.assertNotIn("SECRET_EVAL_EXPECTATION_DO_NOT_SEND", json.dumps(llm.requests))
        self.assertEqual(read_json(self.path / "status.json")["status"], "completed")

    def test_summary_fault_preserves_old_summary_and_original_history(self):
        recorder = Recorder(self.path, fault={"kind": "summary_error", "times": 1})
        with ExitStack() as resources:
            runner, _, _ = assemble(resources, recorder, variant="react", rag_available=False, llm=FakeLLM([message("预算仍是6000")]))
            runner.history.summary = "旧摘要"
            for i in range(10):
                runner.history.add_user(f"第{i}轮；预算6000")
                runner.history.add_assistant("收到")
            before = deepcopy(runner.history.messages)
            runner.run("复述我的预算")
        self.assertEqual(runner.history.summary, "旧摘要")
        self.assertEqual(runner.history.messages[:20], before)
        self.assertEqual(len(runner.history.messages), 22)
        self.assertTrue(any(e["kind"] == "summary" and not e["success"] for e in read_jsonl(recorder.path)))

    def test_parse_injection_skips_execution_and_natural_repair_is_counted(self):
        recorder = Recorder(self.path, fault={"kind": "invalid_json", "tool": "get_product_detail", "times": 1})
        llm = FakeLLM([message(tool="get_product_detail", args='{"product_id":"NB001"}'),
                       message(tool="get_product_detail", args='{"product_id":"NB001"}'), message("NB001 售价6299元")])
        with ExitStack() as resources:
            runner, _, _ = assemble(resources, recorder, variant="react", rag_available=False, llm=llm)
            runner.run("NB001的价格")
        events = read_jsonl(recorder.path)
        requests = [e for e in events if e["kind"] == "model_tool_request"]
        self.assertTrue(requests[0]["injected"])
        self.assertEqual(requests[0]["error_stage"], "parse")
        self.assertFalse(requests[1]["injected"])
        self.assertTrue(requests[1]["valid"])
        self.assertEqual(sum(e["kind"] == "function_start" for e in events), 1)
        self.assertIn("解析失败", llm.requests[1]["messages"][-1]["content"])

    def test_validation_and_local_execution_and_result_contract_are_separate(self):
        for name, fault, raw, expected, entered in [
            ("type", None, '{"product_id":1}', "validation", False),
            ("local", {"kind": "temporary_error", "tool": "get_product_detail"}, '{"product_id":"NB001"}', "execution", True),
            ("json", {"kind": "invalid_result", "tool": "get_product_detail"}, '{"product_id":"NB001"}', "result_contract", True),
        ]:
            path = self.path / name
            path.mkdir()
            recorder = Recorder(path, fault=fault)
            with ExitStack() as resources:
                runner, _, _ = assemble(resources, recorder, variant="react", rag_available=False,
                                        llm=FakeLLM([message(tool="get_product_detail", args=raw), message("无法确认")]))
                runner.run("查规格")
            event = next(e for e in read_jsonl(recorder.path) if e["kind"] == "tool_end")
            self.assertEqual(event["error_stage"], expected)
            self.assertEqual(event["function_entered"], entered)

    def test_unknown_and_non_object_arguments(self):
        from tool_definitions.parameter_schemas import GetProductDetailArgs
        lookup = {"detail": {"args_model": GetProductDetailArgs}}
        self.assertEqual(argument_check("unknown", "{}", lookup)["error_stage"], "lookup")
        self.assertEqual(argument_check("detail", "[]", lookup)["error_stage"], "parse")
        self.assertEqual(argument_check("detail", '{"product_id":"NB001","extra":2}', lookup)["error_stage"], "validation")

    def test_budget_cannot_be_swallowed_by_react_fallback(self):
        recorder = Recorder(self.path, max_calls=1)
        llm = FakeLLM([message(tool="get_product_detail", args='{"product_id":"NB001"}')])
        with ExitStack() as resources:
            runner, _, _ = assemble(resources, recorder, variant="react", rag_available=False, llm=llm)
            with self.assertRaises(BudgetExceeded):
                runner.run("NB001规格")
        self.assertEqual(len(llm.requests), 1)

    def test_failed_and_inflight_usage_is_unknown(self):
        recorder = Recorder(self.path)
        llm = ObservedLLM(FakeLLM([TimeoutError("connection timed out")]), recorder)
        with self.assertRaises(TimeoutError):
            llm.chat(messages=[], tools=[])
        recorder.emit("llm_start", call_id=2, stage="executor")
        usage = usage_totals(read_jsonl(recorder.path))
        self.assertEqual(usage["unknown_usage_calls"], 2)
        self.assertEqual(usage["llm_calls"], 2)

    def test_hard_timeout_kills_worker_and_preserves_inflight_event(self):
        status = supervised(slow_worker, {}, self.path / "worker", case_timeout=10, turn_timeout=.2)
        self.assertEqual(status["status"], "timeout")
        self.assertGreater(status["active_turn_elapsed_ms"], 0)
        self.assertFalse((self.path / "worker/escaped.txt").exists())
        self.assertEqual(usage_totals(read_jsonl(self.path / "worker/events.jsonl"))["unknown_usage_calls"], 1)

    def test_semantics_are_pending_even_when_numbers_and_execution_look_correct(self):
        case = next(c for c in load_cases(ROOT / "data/eval/agent_v1.jsonl") if c["id"] == "single_product-01")
        row = {"key": "test", "variant": "react", "status": "completed"}
        turns = [{"turn": 1, "replies": ["NB002的价格是6299"], "state": {"trace": {"route": {"kind": "agent"}}}}]
        initial = score(case, row, turns, [])
        self.assertIsNone(initial["task_success"])
        review = review_template(case, row, turns, [])
        review.update(reviewer="human", reviewed_at="2026-10-16", notes="数字存在但商品对应错误", grounded=False, forbidden_claims_absent=False)
        review["criteria"]["turn_1.goal"] = False
        self.assertFalse(score(case, row, turns, [], review)["task_success"])
        self.assertEqual(initial["route_checks"], [])  # All-ReAct is not penalized for routing.

    def test_review_identity_and_bool_values_are_checked(self):
        case = load_cases(ROOT / "data/eval/agent_v1.jsonl")[0]
        template = review_template(case, {"key": "one"}, [], [])
        review = deepcopy(template)
        review["evidence_sha256"] = "old"
        with self.assertRaises(ValueError):
            validate_review(review, template)
        review = deepcopy(template)
        review["grounded"] = "true"
        with self.assertRaises(ValueError):
            validate_review(review, template)

    def test_empty_report_has_no_fake_success_or_latency(self):
        metrics = aggregate([])
        self.assertIsNone(metrics["task_success"]["rate"])
        self.assertIsNone(metrics["natural_argument_validity"]["rate"])
        self.assertIsNone(metrics["latency_ms"]["task_p95"])

    def test_rerank_requires_every_candidate_finite_score(self):
        self.assertTrue(valid_scores('{"scores":[{"id":"a","score":0.4},{"id":"b","score":0.1}]}', ["a", "b"]))
        for text in ['{"scores":[{"id":"a","score":1}]}', '{"scores":[{"id":"a","score":NaN}]}',
                     '{"scores":[{"id":"a","score":0.2},{"id":"a","score":0.7}]}']:
            self.assertFalse(valid_scores(text, ["a", "b"]))

    def test_retrieval_metric_uses_chunk_or_parent_id(self):
        items = [{"id": "other", "payload": {}}, {"id": "chunk-2", "payload": {"doc_id": "guide-1"}}]
        self.assertEqual(ranking_metrics(items, ["guide-1"]), {"hit1": 0, "hit5": 1, "hit20": 1, "mrr5": .5})

    def test_memory_missing_fields_do_not_pass_as_unknown(self):
        expected = {"unknown": {"value": None}, "budget": {"value": "6500"}}
        fields = score_fields('{"budget":16500}', expected)
        self.assertFalse(any(f["correct"] for f in fields))
        fields = score_fields('{"unknown":null,"budget":6500}', expected)
        self.assertTrue(all(f["correct"] for f in fields))

    def test_memory_old_value_is_detected(self):
        old = {"budget": {"value": "6500"}}
        new = {"budget": {"value": "7200"}}
        self.assertTrue(score_fields('{"budget":6500}', new, old)[0]["old_value_retained"])

    def test_prices_have_source_and_cached_tokens_are_not_double_counted(self):
        prices = {"model": "test", "currency": "CNY", "date": "2026-10-16", "source": "test-fixture-only",
                  "uncached_input_per_million": 2, "cached_input_per_million": 1, "output_per_million": 4}
        validate_prices(prices)
        usage = {"models": ["test"], "prompt_tokens": 1000000, "cached_tokens": 500000, "completion_tokens": 100000}
        self.assertAlmostEqual(cost(usage, prices), 1.9)
        self.assertIsNone(cost({**usage, "models": ["different-model"]}, prices))
        self.assertIsNone(cost({"models": [], "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0,
                                "unknown_usage_calls": 1, "llm_calls": 1}, prices))

    def test_pair_skips_efficiency_delta_when_one_side_failed(self):
        base = {"case_id": "x", "repeat": 1, "usage": {"llm_calls": 2, "total_tokens": 25},
                "task_elapsed_ms": 20, "score": {"task_success": None}}
        result = paired([{**base, "variant": "routed", "status": "error"}, {**base, "variant": "react", "status": "completed"}])[0]
        self.assertIsNone(result["elapsed_ms_delta"])
        self.assertEqual(result["quality_change"], "pending_review")


if __name__ == "__main__":
    unittest.main()
