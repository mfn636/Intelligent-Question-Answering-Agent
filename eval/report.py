"""Rebuild reports; optional signed-by-reviewer labels and user-supplied prices."""
import argparse
from collections import Counter, defaultdict
from pathlib import Path

from eval.common import append_jsonl, percentile, read_json, read_jsonl, write_json
from eval.scoring import review_template, score, usage_totals, validate_review


def rate(values):
    known = [v for v in values if v is not None]
    return {"numerator": sum(known), "denominator": len(known), "rate": sum(known) / len(known) if known else None}


def aggregate(rows):
    task = rate([r["score"]["task_success"] for r in rows])
    started = [r for r in rows if r["status"] != "skipped"]
    task["planned"] = len(rows)
    task["started"] = len(started)
    task["completed"] = sum(r["status"] == "completed" for r in rows)
    task["pending_review"] = sum(r["score"]["decision"] == "needs_review" for r in rows)
    totals = {key: sum(r["usage"][key] for r in rows) for key in
              ("llm_calls", "failed_llm_calls", "prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens", "unknown_usage_calls")}
    task_latency = [r["task_elapsed_ms"] for r in started if r["task_elapsed_ms"] is not None]
    turn_latency = [value for r in rows for value in r["turn_elapsed_ms"]]
    arg_n = sum(r["score"]["natural_argument_count"] for r in rows)
    arg_ok = sum(r["score"]["natural_arguments_valid"] for r in rows)
    stages = defaultdict(lambda: {"calls": 0, "known_tokens": 0})
    for row in rows:
        for stage, values in row["usage"]["stages"].items():
            for key, value in values.items():
                stages[stage][key] += value
    return {"task_success": task, "execution_status": dict(Counter(r["status"] for r in rows)),
            "scoring_status": dict(Counter(r["score"]["decision"] for r in rows)),
            "tool_plan_coverage": rate([v for r in rows for v in r["score"]["tool_plans"]]),
            "unnecessary_tool_rate": rate([v for r in rows for v in r["score"]["unnecessary_tools"]]),
            "route_expectation": rate([v for r in rows for v in r["score"]["route_checks"]]),
            "subgoal_completion": rate([v for r in rows for v in r["score"]["subgoals"]]),
            "grounded": rate([r["score"]["grounded"] for r in rows]),
            "argument_semantics": rate([r["score"]["argument_semantics_correct"] for r in rows]),
            "failure_stages": dict(Counter(stage for r in rows for stage in r["score"]["failure_stages"])),
            "workflow_requests": sum(r["score"]["workflow_requests"] for r in rows),
            "summary_coverage": rate([r["score"]["summary_coverage"] for r in rows if r.get("minimum_summaries", 0)]),
            "natural_argument_validity": {"numerator": arg_ok, "denominator": arg_n, "rate": arg_ok / arg_n if arg_n else None},
            "latency_ms": {"method": "linear interpolation (n-1)*q", "task_n": len(task_latency),
                           "task_p50": percentile(task_latency, .5), "task_p95": percentile(task_latency, .95),
                           "turn_n": len(turn_latency), "turn_p50": percentile(turn_latency, .5), "turn_p95": percentile(turn_latency, .95)},
            "usage": totals, "stages": dict(stages),
            "cost": {"known_amount": sum(r.get("known_cost") or 0 for r in rows) if any(r.get("known_cost") is not None for r in rows) else None,
                     "unknown_usage_calls": totals["unknown_usage_calls"],
                     "unpriced_cases": sum(r.get("known_cost") is None for r in started)}}


def paired(rows):
    groups = defaultdict(dict)
    for row in rows:
        groups[(row["case_id"], row["repeat"])][row["variant"]] = row
    results = []
    for (case_id, repeat), variants in groups.items():
        if set(variants) != {"routed", "react"}:
            continue
        a, b = variants["react"], variants["routed"]
        comparable = a["status"] == b["status"] == "completed"
        reviewed = all(x["score"]["task_success"] is not None for x in (a, b))
        results.append({"case_id": case_id, "repeat": repeat, "both_completed": comparable,
                        "llm_calls_delta_routed_minus_react": b["usage"]["llm_calls"] - a["usage"]["llm_calls"] if comparable else None,
                        "known_tokens_delta": b["usage"]["total_tokens"] - a["usage"]["total_tokens"] if comparable else None,
                        "elapsed_ms_delta": b["task_elapsed_ms"] - a["task_elapsed_ms"] if comparable else None,
                        "quality_change": ("fixed" if b["score"]["task_success"] else "regressed")
                        if reviewed and a["score"]["task_success"] != b["score"]["task_success"] else "unchanged" if reviewed else "pending_review"})
    return results


def validate_prices(prices):
    import math
    required = {"model", "currency", "date", "source", "uncached_input_per_million", "cached_input_per_million", "output_per_million"}
    if set(prices) != required or any(not prices[k] for k in ("model", "currency", "date", "source")):
        raise ValueError("Pricing needs model, currency, date, source and three per-million token rates")
    for key in required - {"model", "currency", "date", "source"}:
        if type(prices[key]) not in (int, float) or not math.isfinite(prices[key]) or prices[key] < 0:
            raise ValueError("Prices must be finite non-negative numbers")


def cost(usage, prices):
    if prices is None or (usage["models"] and usage["models"] != [prices["model"]]):
        return None
    # An all-failed run has no model identity or known tokens: zero would falsely
    # claim that the request was free. Mixed runs may expose a known partial amount,
    # while unknown calls remain visible in the report denominator.
    if not usage["models"] and (usage.get("unknown_usage_calls", 0) or usage.get("llm_calls", 0)):
        return None
    return ((usage["prompt_tokens"] - usage["cached_tokens"]) * prices["uncached_input_per_million"]
            + usage["cached_tokens"] * prices["cached_input_per_million"]
            + usage["completion_tokens"] * prices["output_per_million"]) / 1_000_000


def generate_report(directory, reviews_path=None, prices=None):
    directory = Path(directory)
    if prices:
        validate_prices(prices)
    cases = {c["id"]: c for c in read_json(directory / "scenarios.json")}
    reviews = read_jsonl(reviews_path) if reviews_path else []
    review_map = {r["key"]: r for r in reviews}
    if len(review_map) != len(reviews):
        raise ValueError("Duplicate review keys")
    rows, templates = [], []
    for row in read_jsonl(directory / "cases.jsonl"):
        case = cases[row["case_id"]]
        events = read_jsonl(directory / row["key"] / "events.jsonl")
        turns = read_jsonl(directory / row["key"] / "turns.jsonl")
        template = review_template(case, row, turns, events)
        templates.append(template)
        review = review_map.get(row["key"])
        if review:
            validate_review(review, template)
        usage = usage_totals(events)
        latency = [t["elapsed_ms"] for t in turns]
        if row.get("active_turn_elapsed_ms") is not None:
            latency.append(row["active_turn_elapsed_ms"])
        if not latency and row.get("worker_elapsed_ms") is not None:
            # A timeout can happen while Runner is inside a model call, before a turn is saved.
            # Keep its outer elapsed time visible rather than treating it as zero.
            latency.append(row["worker_elapsed_ms"])
        rows.append({**row, "score": score(case, row, turns, events, review), "usage": usage,
                     "minimum_summaries": case["minimum_summaries"],
                     "turn_elapsed_ms": latency, "task_elapsed_ms": sum(latency) if latency else None, "known_cost": cost(usage, prices)})
    if set(review_map) - {r["key"] for r in rows}:
        raise ValueError("Review contains keys outside this run")
    groups = defaultdict(list)
    for row in rows:
        group = f"{row['variant']}/{row['split']}/{'fault' if row['fault'] else 'normal'}"
        groups[group].append(row)
        groups[group + "/" + row["category"]].append(row)
    metrics = {"pricing": prices, "groups": {k: aggregate(v) for k, v in groups.items()}, "pairs": paired(rows), "cases": rows}
    # Repetition stability is descriptive; never select the best repetition.
    stability = defaultdict(list)
    for row in rows:
        stability[f"{row['case_id']}/{row['variant']}"].append(row["score"]["task_success"])
    metrics["repeat_stability"] = {k: {"outcomes": v, "all_passed": all(v) if all(x is not None for x in v) else None} for k, v in stability.items()}
    write_json(directory / "metrics.json", metrics)
    template_path = directory / "review_template.jsonl"
    if not template_path.exists():
        for template in templates:
            append_jsonl(template_path, template)
    if reviews_path:
        write_json(directory / "applied_reviews.json", reviews)
    text = ["# Agent evaluation", "", "Draft synthetic labels require human review. Completed execution does not imply task success.",
            "Latency excludes worker startup and user thinking; each scenario uses a cold worker. P95 is descriptive, not a concurrency SLA.",
            "Failed/in-flight API calls without usage have unknown cost. SDK internal retries are not separately observable. Budget-truncated cases remain unassessed; they are not Agent quality failures.", "",
            "| Group | Started / planned | Assessed | Passed | Success rate | Pending | Task P50 / P95 ms | Calls | Known tokens | Unknown usage |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    fmt = lambda n: "—" if n is None else f"{n:.1f}"
    for name, metrics_group in metrics["groups"].items():
        task, lat, use = metrics_group["task_success"], metrics_group["latency_ms"], metrics_group["usage"]
        success = "—" if task["rate"] is None else f"{task['rate']:.1%}"
        text.append(f"| {name} | {task['started']}/{task['planned']} | {task['denominator']} | {task['numerator']} | {success} | {task['pending_review']} | {fmt(lat['task_p50'])} / {fmt(lat['task_p95'])} | {use['llm_calls']} | {use['total_tokens']} | {use['unknown_usage_calls']} |")
    text += ["", "## Process diagnostics", "", "Ratios below show numerator/denominator; argument validity excludes workflow requests and injected requests.", "",
             "| Group | Tool plan coverage | Unnecessary tools | Natural arguments valid | Grounded | Argument semantics |",
             "| --- | --- | --- | --- | --- | --- |"]
    for name, m in metrics["groups"].items():
        ratios = [f"{m[key]['numerator']}/{m[key]['denominator']}" for key in
                  ("tool_plan_coverage", "unnecessary_tool_rate", "natural_argument_validity", "grounded", "argument_semantics")]
        text.append(f"| {name} | " + " | ".join(ratios) + " |")
    text += ["", "## Paired outcomes", "", "Deltas are routed minus ReAct, computed only when both executions completed; quality still requires review.", ""]
    for pair in metrics["pairs"]:
        text.append(f"- {pair['case_id']} / repeat {pair['repeat']}: calls Δ {pair['llm_calls_delta_routed_minus_react']}, known tokens Δ {pair['known_tokens_delta']}, latency Δ {fmt(pair['elapsed_ms_delta'])} ms; {pair['quality_change']}.")
    text += ["", "## Review and failure diagnosis", "", "Copy review_template.jsonl to reviews.jsonl; inspect scenarios.json, each turns.jsonl and events.jsonl. Fill booleans, reviewer, date and reasons; leave uncertain judgments null. Run python -m eval.report RUN_DIR --reviews REVIEWS_FILE.", "",
             "Fault criteria: invalid requests must not enter business functions; execution errors must stay distinguishable from empty results; temporary errors may recover, persistent errors must not yield fabricated success. Verify actual injection events before scoring. Summary faults must preserve old summary and original messages.", ""]
    for row in rows:
        text.append(f"- {row['key']}: execution={row['status']}; score={row['score']['decision']}; summaries={row['score']['successful_summaries']}; summary coverage={row['score']['summary_coverage']}.")
    (directory / "report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--prices", type=Path)
    args = parser.parse_args()
    generate_report(args.run_dir, args.reviews, read_json(args.prices) if args.prices else None)
    print(args.run_dir / "report.md")


if __name__ == "__main__":
    main()
