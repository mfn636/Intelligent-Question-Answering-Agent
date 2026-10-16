"""python -m eval.run_agent --suite pilot --variants routed react --limit 3"""
import argparse
from pathlib import Path

from eval.common import ROOT, append_jsonl, file_hash, new_run, read_json, read_jsonl, supervised, text_file_hash, write_json
from eval.runtime import agent_worker, probe_worker
from eval.schema import load_cases


def scheduled(cases, variants, repeats):
    for repeat in range(1, repeats + 1):
        for index, case in enumerate(cases):
            # Alternate paired order to reduce time-of-run bias; retain all repetitions.
            order = variants if (repeat + index) % 2 else list(reversed(variants))
            for variant in order:
                yield case, variant, repeat


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["pilot", "dev", "acceptance", "all"], default="pilot")
    parser.add_argument("--variants", nargs="+", choices=["routed", "react"], default=["routed"])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--max-calls", type=int, default=150)
    parser.add_argument("--max-tokens", type=int, default=150000)
    parser.add_argument("--turn-timeout", type=float, default=90)
    parser.add_argument("--case-timeout", type=float, default=600)
    parser.add_argument("--rag", choices=["auto", "skip"], default="auto")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    if min(args.repeats, args.max_calls, args.max_tokens, args.turn_timeout, args.case_timeout) <= 0 or (args.limit is not None and args.limit < 1):
        parser.error("Counts, limits and deadlines must be positive")
    if len(args.variants) != len(set(args.variants)):
        parser.error("Duplicate variants")
    dataset = ROOT / "data/eval/agent_v1.jsonl"
    manifest = read_json(ROOT / "data/eval/manifest.json")
    if manifest.get("hash_format") != "sha256-lf":
        parser.error("Unsupported fixture fingerprint format")
    if text_file_hash(dataset) != manifest["dataset_sha256"]:
        parser.error("Dataset changed without updating its manifest")
    for source, expected_hash in manifest["sources"].items():
        if text_file_hash(ROOT / source) != expected_hash:
            parser.error(f"Fixture drift: {source}; review and version labels before running")
    cases = load_cases(dataset)
    if args.suite == "pilot":
        cases = [c for c in cases if c["pilot"]]
    elif args.suite != "all":
        cases = [c for c in cases if c["split"] == args.suite]
    if args.ids:
        missing = set(args.ids) - {c["id"] for c in cases}
        if missing:
            parser.error(f"Unknown IDs in selected suite: {sorted(missing)}")
        cases = [c for c in cases if c["id"] in args.ids]
    if args.limit:
        cases = cases[:args.limit]
    if args.validate_only:
        print(f"Validated {len(cases)} selected draft scenarios; no model calls.")
        return
    directory = new_run("agent", {**vars(args), "dataset_sha256": file_hash(dataset), "manifest": manifest,
                                  "sampling": "Production parameters, no overrides; per-scenario cold worker",
                                  "token_budget": "Stop before next call once observed usage reaches limit; one response may exceed it; unknown usage is excluded"})
    write_json(directory / "scenarios.json", cases)
    rag_status = {"status": "skipped", "reason": "requested"}
    if args.rag == "auto":
        print("Checking read-only knowledge dependencies...", flush=True)
        rag_status = supervised(probe_worker, {}, directory / "preflight", case_timeout=30, turn_timeout=30)
    write_json(directory / "dependencies.json", rag_status)
    used_calls = used_tokens = 0
    schedule = list(scheduled(cases, args.variants, args.repeats))
    write_json(directory / "schedule.json", [{"case_id": case["id"], "variant": variant, "repeat": repeat}
                                            for case, variant, repeat in schedule])
    try:
        for case, variant, repeat in schedule:
            key = f"{case['id']}--{variant}--{repeat}"
            metadata = {"key": key, "case_id": case["id"], "variant": variant, "repeat": repeat,
                        "split": case["split"], "category": case["category"], "fault": case["fault"] is not None}
            if used_calls >= args.max_calls or used_tokens >= args.max_tokens:
                status = {"status": "skipped", "reason": "global_budget"}
            elif "requires_rag" in case["tags"] and rag_status["status"] != "completed":
                status = {"status": "skipped", "reason": "rag_unavailable"}
            else:
                payload = {"case": case, "variant": variant, "rag_available": rag_status["status"] == "completed",
                           "max_calls": args.max_calls - used_calls, "max_tokens": args.max_tokens - used_tokens}
                status = supervised(agent_worker, payload, directory / key,
                                    case_timeout=args.case_timeout, turn_timeout=args.turn_timeout)
                events = read_jsonl(directory / key / "events.jsonl")
                used_calls += sum(e["kind"] == "llm_start" for e in events)
                used_tokens += sum((e.get("usage") or {}).get("total_tokens", 0) for e in events if e["kind"] == "llm_end")
            append_jsonl(directory / "cases.jsonl", {**metadata, **status})
            print(f"{key}: {status['status']} | calls={used_calls}, known tokens={used_tokens}", flush=True)
    except KeyboardInterrupt:
        print("Interrupted; active worker stopped, preserving partial evidence.", flush=True)
    finally:
        completed = {r["key"] for r in read_jsonl(directory / "cases.jsonl")}
        for case, variant, repeat in schedule:
            key = f"{case['id']}--{variant}--{repeat}"
            if key not in completed:
                append_jsonl(directory / "cases.jsonl", {
                    "key": key, "case_id": case["id"], "variant": variant, "repeat": repeat,
                    "split": case["split"], "category": case["category"], "fault": case["fault"] is not None,
                    "status": "interrupted" if (directory / key).exists() else "skipped", "reason": "run_interrupted"})
        from eval.report import generate_report
        generate_report(directory)
        print(f"Report: {directory / 'report.md'}", flush=True)


if __name__ == "__main__":
    main()
