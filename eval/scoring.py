"""Structural checks are automatic; semantic judgments remain explicit reviews."""
from eval.common import digest


def contains(actual, expected):
    return isinstance(actual, dict) and all(actual.get(k) == v for k, v in expected.items())


def plan_satisfied(plan, events):
    """Unordered coverage, distinct calls; successful lookup alone is not task success."""
    remaining = [e for e in events if e["kind"] == "tool_end" and e.get("error") is None]
    for step in plan:
        index = next((i for i, event in enumerate(remaining)
                      if event["name"] == step["tool"] and contains(event["args"], step["args"])), None)
        if index is None:
            return False
        remaining.pop(index)
    return True


def review_template(case, row, turns, events):
    return {
        "key": row["key"], "case_sha256": digest(case), "evidence_sha256": digest({"row": row, "turns": turns, "events": events}),
        "reviewer": None, "reviewed_at": None, "notes": "",
        "criteria": {f"turn_{i}.{check['id']}": None for i, turn in enumerate(case["turns"], 1) for check in turn["checks"]},
        "grounded": None, "forbidden_claims_absent": None, "argument_semantics_correct": None,
        "fault_handling": None,
    }


def validate_review(review, template):
    if set(review) != set(template):
        raise ValueError("Review fields differ from template: " + template["key"])
    for key in ("key", "case_sha256", "evidence_sha256"):
        if review[key] != template[key]:
            raise ValueError(f"Stale/mismatched review {key}: {template['key']}")
    if set(review["criteria"]) != set(template["criteria"]):
        raise ValueError("Review criteria differ from scenario")
    values = [*review["criteria"].values(), *(review[k] for k in ("grounded", "forbidden_claims_absent", "argument_semantics_correct", "fault_handling"))]
    if any(value is not None and type(value) is not bool for value in values):
        raise ValueError("Judgments must be true, false or null")
    if any(value is not None for value in values) and (not review["reviewer"] or not review["reviewed_at"] or not review["notes"].strip()):
        raise ValueError("Completed judgments need reviewer, date and evidence-based notes")


def score(case, row, turns, events, review=None):
    route_checks, plans, needless = [], [], []
    for i, expected in enumerate(case["turns"], 1):
        actual = next((t for t in turns if t["turn"] == i), None)
        per_turn = [e for e in events if e.get("turn") == i]
        if not actual:
            continue
        route = actual["state"]["trace"]["route"].get("kind")
        if row["variant"] == "routed":
            route_checks.append(route in expected["allowed_routes"])
        if expected["acceptable_tool_plans"]:
            plans.append(any(plan_satisfied(plan, per_turn) for plan in expected["acceptable_tool_plans"]))
        if expected["no_tools"]:
            needless.append(any(e["kind"] in ("tool_start", "model_tool_request") for e in per_turn))
    natural_args = [e for e in events if e["kind"] == "model_tool_request" and not e["injected"]]
    successful_summaries = sum(e["kind"] == "summary" and e["success"] for e in events)
    injected = any(e["kind"] == "fault_injected" for e in events)
    task_success = None
    if row["status"] == "skipped":
        decision = "skipped"
    elif case["fault"] and not injected:
        decision = "fault_not_exercised"
    elif row["status"] in ("budget_exhausted", "interrupted"):
        decision = "incomplete_" + row["status"]
    elif row["status"] in ("timeout", "worker_error") or (row["status"] != "completed" and not case["fault"]):
        decision, task_success = "failed_execution", False
    elif review is None:
        decision = "needs_review"
    else:
        values = [review["grounded"], review["forbidden_claims_absent"]]
        values += [review["fault_handling"]] if case["fault"] else list(review["criteria"].values())
        if any(value is False for value in values):
            decision, task_success = "reviewed_fail", False
        elif any(value is None for value in values):
            decision = "needs_review"
        else:
            decision, task_success = "reviewed_pass", True
    return {"decision": decision, "task_success": task_success,
            "subgoals": list(review["criteria"].values()) if review else [],
            "route_checks": route_checks, "tool_plans": plans, "unnecessary_tools": needless,
            "natural_argument_count": len(natural_args), "natural_arguments_valid": sum(e["valid"] for e in natural_args),
            "injected_requests": sum(e["kind"] == "model_tool_request" and e["injected"] for e in events),
            "successful_summaries": successful_summaries,
            "summary_coverage": successful_summaries >= case["minimum_summaries"],
            "fault_exercised": injected if case["fault"] else None,
            "failure_stages": [e["error_stage"] for e in events if e["kind"] == "tool_end" and e.get("error_stage")],
            "workflow_requests": sum(e["kind"] == "tool_start" and e.get("source") == "workflow" for e in events),
            "grounded": review["grounded"] if review else None,
            "argument_semantics_correct": review["argument_semantics_correct"] if review else None}


def usage_totals(events):
    starts = [e for e in events if e["kind"] == "llm_start"]
    ends = {e["call_id"]: e for e in events if e["kind"] == "llm_end"}
    totals = {key: 0 for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens")}
    unknown, injected, failed = 0, 0, 0
    stages = {}
    models = set()
    for start in starts:
        end = ends.get(start["call_id"])
        unknown += int(end is None or end.get("usage_unknown", True))
        injected += int(bool(end and end.get("injected")))
        failed += int(end is None or bool(end.get("error")))
        usage = (end or {}).get("usage") or {}
        if usage.get("model"):
            models.add(usage["model"])
        stage = stages.setdefault(start["stage"], {"calls": 0, "known_tokens": 0})
        stage["calls"] += 1
        stage["known_tokens"] += usage.get("total_tokens", 0)
        for key in totals:
            totals[key] += usage.get(key, 0)
    return {**totals, "llm_calls": len(starts), "failed_llm_calls": failed,
            "injected_llm_calls": injected, "unknown_usage_calls": unknown, "stages": stages, "models": sorted(models)}
