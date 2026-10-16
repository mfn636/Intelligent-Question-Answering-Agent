"""Controlled fixed-transcript diagnostic; does not measure shopping task success."""
import argparse
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import time

from eval.common import (ROOT, BudgetExceeded, append_jsonl, file_hash, new_run, read_json,
                         read_jsonl, safe_error, supervised, write_json)
from eval.recorders import ObservedLLM, ObservedSummarizer, Recorder
from eval.scoring import usage_totals


def same_value(actual, expected):
    if expected is None:
        return actual is None
    if isinstance(actual, (dict, list, bool)) or actual is None:
        return False
    try:
        return Decimal(str(actual)) == Decimal(str(expected))
    except InvalidOperation:
        return str(actual).strip().casefold() == str(expected).strip().casefold()


def score_fields(answer, expected, previous=None):
    try:
        parsed = json.loads(answer)
        if not isinstance(parsed, dict):
            raise ValueError("not an object")
    except (ValueError, TypeError):
        parsed = {}
    rows = []
    for name, label in expected.items():
        present = name in parsed
        actual = parsed.get(name)
        correct = present and any(same_value(actual, value) for value in [label["value"], *label.get("aliases", [])])
        old = (previous or {}).get(name, {}).get("value")
        changed = previous is not None and not same_value(old, label["value"])
        rows.append({"field": name, "expected": label["value"], "actual": actual, "present": present,
                     "correct": correct, "unknown_expected": label["value"] is None,
                     "unknown_fabricated": label["value"] is None and present and actual is not None,
                     "old_value_retained": changed and present and same_value(actual, old)})
    return rows


def memory_worker(payload, directory):
    from bootstrap import _build_llm
    from agent.memory.history import ConversationHistory
    from agent.memory.summary import ConversationSummarizer
    from prompts.memory import SUMMARY_CONTEXT_PREFIX
    path = Path(directory)
    recorder = Recorder(path, max_calls=payload["max_calls"], max_tokens=payload["max_tokens"])
    status = {"status": "completed"}
    try:
        with ExitStack() as resources:
            llm = ObservedLLM(_build_llm(resources), recorder, stage="memory_probe")
            summarizer = ObservedSummarizer(ConversationSummarizer(llm=llm.for_stage("summary")), recorder)
            history = ConversationHistory()
            checkpoints = {cp["turn"]: cp for cp in payload["case"]["checkpoints"]}
            previous = None
            for number, turn in enumerate(payload["case"]["turns"], 1):
                recorder.turn = number
                recorder.emit("turn_start", user=turn["user"])
                started = time.perf_counter()
                history.add_user(turn["user"])
                if payload["strategy"] == "summary":
                    before = deepcopy(history.messages)
                    old_summary = history.summary
                    try:
                        history.compact(summarizer, keep=10, trigger=20)
                    except Exception as exc:
                        recorder.emit("summary_retention", preserved=history.messages == before and history.summary == old_summary, error=safe_error(exc))
                if number in checkpoints:
                    messages = [{"role": "system", "content": "只依据给出的对话提取用户最新有效信息，严格按本轮用户要求返回 JSON；未知填 null。"}]
                    if history.summary:
                        messages.append({"role": "assistant", "content": SUMMARY_CONTEXT_PREFIX + history.summary})
                    recent = history.messages[-10:] if payload["strategy"] == "recent10" else history.messages
                    messages.extend(deepcopy(recent))
                    response = llm.chat(messages=messages, temperature=0, response_format={"type": "json_object"}, thinking=False)
                    expected = checkpoints[number]["expected"]
                    append_jsonl(path / "checkpoints.jsonl", {"turn": number, "answer": response.content,
                                 "fields": score_fields(response.content, expected, previous), "summary": history.summary})
                    previous = expected
                # Same prewritten assistant transcript for EVERY strategy; outputs never feed back.
                history.add_assistant(turn["assistant"])
                recorder.emit("turn_end", elapsed_ms=(time.perf_counter() - started) * 1000)
    except (Exception, BudgetExceeded) as exc:
        status = {"status": "budget_exhausted" if isinstance(exc, BudgetExceeded) else "error", "error": safe_error(exc)}
    finally:
        write_json(path / "status.json", status)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--strategies", nargs="+", choices=["full", "recent10", "summary"], default=["full", "recent10", "summary"])
    parser.add_argument("--max-calls", type=int, default=180)
    parser.add_argument("--max-tokens", type=int, default=250000)
    parser.add_argument("--case-timeout", type=float, default=300)
    parser.add_argument("--turn-timeout", type=float, default=60)
    args = parser.parse_args(argv)
    if min(args.limit, args.max_calls, args.max_tokens, args.case_timeout, args.turn_timeout) <= 0 or len(set(args.strategies)) != len(args.strategies):
        parser.error("Positive limits and unique strategies required")
    data_file = ROOT / "data/memory_golden.json"
    data = read_json(data_file)
    cases = data["cases"][:args.limit]
    directory = new_run("memory", {**vars(args), "dataset_sha256": file_hash(data_file), "authorship": data.get("authorship"),
                                    "protocol": "Fixed assistant history; identical JSON extraction prompt; all summary calls counted"})
    used_calls = used_tokens = 0
    rows = []
    for index, case in enumerate(cases):
        for strategy in args.strategies if index % 2 == 0 else list(reversed(args.strategies)):
            key = case["id"] + "--" + strategy
            if used_calls >= args.max_calls or used_tokens >= args.max_tokens:
                status = {"status": "skipped", "reason": "global_budget"}
            else:
                status = supervised(memory_worker, {"case": case, "strategy": strategy,
                                    "max_calls": args.max_calls - used_calls, "max_tokens": args.max_tokens - used_tokens},
                                    directory / key, case_timeout=args.case_timeout, turn_timeout=args.turn_timeout)
            events = read_jsonl(directory / key / "events.jsonl")
            usage = usage_totals(events)
            used_calls += usage["llm_calls"]
            used_tokens += usage["total_tokens"]
            checkpoints = read_jsonl(directory / key / "checkpoints.jsonl")
            fields = [f for cp in checkpoints for f in cp["fields"]]
            row = {"key": key, "strategy": strategy, **status, "usage": usage, "fields": fields,
                   "expected_fields": sum(len(cp["expected"]) for cp in case["checkpoints"]),
                   "checkpoints_completed": len(checkpoints), "checkpoints_planned": len(case["checkpoints"]),
                   "summaries": sum(e["kind"] == "summary" and e["success"] for e in events),
                   "summary_failures": sum(e["kind"] == "summary" and not e["success"] for e in events),
                   "retention": [e for e in events if e["kind"] == "summary_retention"]}
            rows.append(row)
            append_jsonl(directory / "cases.jsonl", row)
            print(key + ": " + status["status"], flush=True)
    metrics = {}
    for strategy in args.strategies:
        selected = [r for r in rows if r["strategy"] == strategy]
        fields = [f for r in selected for f in r["fields"]]
        planned = sum(r["expected_fields"] for r in selected)
        metrics[strategy] = {"correct": sum(f["correct"] for f in fields), "scored_fields": len(fields), "planned_fields": planned,
                             "accuracy_scored": sum(f["correct"] for f in fields) / len(fields) if fields else None,
                             "unknown_fabricated": sum(f["unknown_fabricated"] for f in fields),
                             "unknown_expected_scored": sum(f["unknown_expected"] for f in fields),
                             "old_values_retained": sum(f["old_value_retained"] for f in fields),
                             "summary_failures": sum(r["summary_failures"] for r in selected),
                             "calls": sum(r["usage"]["llm_calls"] for r in selected),
                             "known_tokens": sum(r["usage"]["total_tokens"] for r in selected),
                             "unknown_usage_calls": sum(r["usage"]["unknown_usage_calls"] for r in selected)}
    write_json(directory / "metrics.json", metrics)
    lines = ["# Controlled memory diagnostic", "", "Synthetic fixed-transcript extraction, not end-to-end task success. All summary calls count toward usage. Missing checkpoints are unscored, not successful null answers.", "",
             "| Strategy | Correct / scored / planned fields | Calls | Known tokens | Unknown usage | Fabricated unknowns | Old values |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for key, m in metrics.items():
        lines.append(f"| {key} | {m['correct']}/{m['scored_fields']}/{m['planned_fields']} | {m['calls']} | {m['known_tokens']} | {m['unknown_usage_calls']} | {m['unknown_fabricated']} | {m['old_values_retained']} |")
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(directory / "report.md")


if __name__ == "__main__":
    main()
