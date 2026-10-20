"""Run the manually designed Agent behavior set separately from the draft suite."""
import argparse
from pathlib import Path

from eval.common import (ROOT, append_jsonl, new_run, read_json, read_jsonl,
                         supervised, write_json)
from eval.runtime import agent_worker


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variants", nargs="+", choices=["routed", "react"], default=["routed", "react"])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-calls", type=int, default=100)
    parser.add_argument("--max-tokens", type=int, default=100000)
    parser.add_argument("--turn-timeout", type=float, default=90)
    parser.add_argument("--case-timeout", type=float, default=600)
    args = parser.parse_args(argv)
    if args.repeats < 1 or args.max_calls < 1 or args.max_tokens < 1:
        parser.error("budgets and repeats must be positive")

    dataset = ROOT / "data/eval/agent_golden_v1.jsonl"
    cases = [__import__("json").loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    directory = new_run("agent-golden", {
        **vars(args), "dataset": str(dataset.relative_to(ROOT)),
        "evaluation_type": "human-designed behavior set; raw outputs pending human labels",
    })
    write_json(directory / "scenarios.json", cases)
    schedule = [(case, variant, repeat)
                for repeat in range(1, args.repeats + 1)
                for case in cases
                for variant in args.variants]
    write_json(directory / "schedule.json", [
        {"case_id": c["id"], "variant": v, "repeat": r} for c, v, r in schedule
    ])

    for case, variant, repeat in schedule:
        key = f"{case['id']}--{variant}--{repeat}"
        payload = {"case": case, "variant": variant, "rag_available": False,
                   "max_calls": args.max_calls, "max_tokens": args.max_tokens}
        status = supervised(agent_worker, payload, directory / key,
                            case_timeout=args.case_timeout, turn_timeout=args.turn_timeout)
        append_jsonl(directory / "cases.jsonl", {
            "key": key, "case_id": case["id"], "variant": variant, "repeat": repeat,
            "split": case["split"], "category": case["category"], "fault": False, **status,
        })
        print(f"{key}: {status['status']}", flush=True)

    rows = []
    for row in read_jsonl(directory / "cases.jsonl"):
        turns = read_jsonl(directory / row["key"] / "turns.jsonl")
        events = read_jsonl(directory / row["key"] / "events.jsonl")
        final = " ".join(reply for turn in turns for reply in turn.get("replies", []) if reply)
        tools = [e for e in events if e.get("kind") == "tool_end"]
        rows.append({**row, "user": next(c["turns"][0]["user"] for c in cases if c["id"] == row["case_id"]),
                     "expected": next(c["turns"][0]["checks"][0]["requirement"] for c in cases if c["id"] == row["case_id"]),
                     "replies": final, "tool_calls": len(tools), "manual_result": None,
                     "manual_notes": ""})
    write_json(directory / "golden_results.json", rows)
    lines = ["# Agent 人工行为基准集运行结果", "", "结果尚未人工判定；`manual_result` 需要依据 scenarios.json 中的期望行为填写。", "",
             "| Case | Variant | Status | Tool calls | Reply | Manual result |", "| --- | --- | --- | ---: | --- | --- |"]
    for row in rows:
        reply = row["replies"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {row['case_id']} | {row['variant']} | {row['status']} | {row['tool_calls']} | {reply} |  |")
    (directory / "golden_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Results: {directory / 'golden_report.md'}", flush=True)


if __name__ == "__main__":
    main()
