"""Verify the published run without Qdrant, model requests or the original logs."""

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MODES = ("vector", "hybrid", "hybrid_rerank")
KEYS = ("hit1", "hit5", "hit20", "mrr5")
PAIRS = (("vector", "hybrid"), ("hybrid", "hybrid_rerank"), ("vector", "hybrid_rerank"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def jsonl(name):
    return [json.loads(line) for line in (ROOT / name).read_text(encoding="utf-8").splitlines()]


def close(actual, expected, label):
    require(math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10),
            f"{label}: {actual} != {expected}")


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(rows, mode):
    return {key: math.fsum(row["metrics"][mode][key] for row in rows) / len(rows) for key in KEYS}


def verify_bootstrap(rows, analysis):
    # Optional dependency; the standard integrity check below is stdlib-only.
    import numpy as np

    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row["expected_ids"])].append(row)
    grouped = list(groups.values())
    require(len(grouped) == 95 and all(len(group) == 2 for group in grouped), "Target clusters changed")
    sizes = np.array([len(group) for group in grouped])
    for left, right in PAIRS:
        rng = np.random.default_rng(analysis["bootstrap"]["seed"])
        samples = rng.integers(0, len(grouped), size=(analysis["bootstrap"]["resamples"], len(grouped)))
        for key in KEYS:
            differences = np.array([
                sum(row["metrics"][right][key] - row["metrics"][left][key] for row in group)
                for group in grouped
            ])
            sampled = 100 * differences[samples].sum(axis=1) / sizes[samples].sum(axis=1)
            bounds = np.percentile(sampled, [2.5, 97.5])
            expected = analysis["pairs"][f"{left}_to_{right}"][key]["paired_cluster_bootstrap_95_ci_pp"]
            for actual, saved in zip(bounds, expected):
                close(float(actual), saved, f"Bootstrap {left}/{right}/{key}")
    print("Verified document-cluster bootstrap intervals (10,000 resamples).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true", help="Recompute saved intervals with NumPy")
    args = parser.parse_args()
    manifest = read("manifest.json")
    present = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*")
               if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts}
    require(present == set(manifest["files"]), "Published file inventory differs from manifest")
    for name, expected in manifest["files"].items():
        payload = (ROOT / name).read_bytes()
        require(len(payload) == expected["bytes"], f"Size mismatch: {name}")
        require(hashlib.sha256(payload).hexdigest() == expected["sha256"], f"Hash mismatch: {name}")

    config, queries = read("config.json"), read("queries.json")
    metrics, analysis = read("metrics.json"), read("analysis.json")
    corpus, prompt = read("candidate_payloads.json"), read("rerank_prompt.json")
    rows, calls = jsonl("rankings.jsonl"), jsonl("rerank_calls.jsonl")
    require(config["run_id"] == analysis["run_id"] == manifest["run_id"], "Run IDs differ")
    require(metrics["status"]["status"] == "completed", "Run is incomplete")
    require(len(rows) == len(queries) == len(calls) == metrics["completed"] == metrics["planned"] == 190,
            "Incomplete query or model-call coverage")
    require([row["query_index"] for row in rows] == list(range(1, 191)), "Query sequence differs")
    require([call["query_index"] for call in calls] == list(range(1, 191)), "Call sequence differs")
    require(len({call["call_id"] for call in calls}) == 190, "Duplicate model calls")
    require(len({row["query"] for row in rows}) == 190, "Duplicate queries")
    used_ids = set()

    for label, row, call in zip(queries, rows, calls):
        require(label == {"query": row["query"], "expected_ids": row["expected_ids"]}, "Labels differ")
        require(len(row["expected_ids"]) == 1, "Expected one designated target")
        require(row["rerank_status"] == "verified", "Unverified rerank")
        require(call["stage"] == "rerank" and call["error"] is None
                and not call["usage_unknown"] and not call["injected"], "Failed or injected call")
        for mode in MODES:
            items = row["results"][mode]
            ids = [item["id"] for item in items]
            require(len(ids) == len(set(ids)) == config["recall_n"] == 20, "Candidate count differs")
            used_ids.update(ids)
            rank = next((i for i, item in enumerate(items, 1)
                         if {item["id"], corpus[item["id"]].get("doc_id")} & set(row["expected_ids"])),
                        math.inf)
            recalculated = {"hit1": int(rank <= 1), "hit5": int(rank <= 5), "hit20": int(rank <= 20),
                            "mrr5": 1 / rank if rank <= 5 else 0}
            for key, value in recalculated.items():
                close(value, row["metrics"][mode][key], f"Query {row['query_index']} {mode}/{key}")

        hybrid = row["results"]["hybrid"]
        scores = json.loads(call["content"])["scores"]
        ids = [entry["id"] for entry in scores]
        require(len(ids) == len(set(ids)) == 20 and set(ids) == {item["id"] for item in hybrid},
                "Rerank scores do not cover exactly the candidates")
        require(all(type(entry["score"]) in (float, int) and math.isfinite(entry["score"])
                    and 0 <= entry["score"] <= 1 for entry in scores), "Invalid rerank score")
        score_map = {entry["id"]: entry["score"] for entry in scores}
        expected_order = sorted(hybrid, key=lambda item: score_map[item["id"]], reverse=True)
        require(expected_order == row["results"]["hybrid_rerank"], "Score-derived ranking differs")
        snippets = [f'[{item["id"]}] {corpus[item["id"]].get("_text", "")[:180]}' for item in hybrid]
        messages = [{"role": "system", "content": prompt["system"]},
                    {"role": "user", "content": prompt["template"].format(query=row["query"], cands="\n".join(snippets))}]
        canonical = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        require(hashlib.sha256(canonical).hexdigest() == call["messages_sha256"], "Request fingerprint differs")
        require(call["parameters"] == {"temperature": 0.0, "max_tokens": 800, "stream": False,
                                       "thinking": False, "response_format": {"type": "json_object"}},
                "Rerank parameters differ")
    require(used_ids == set(corpus), "Candidate corpus differs from observed candidates")
    require(used_ids <= set(read("index_audit.json")["ids"]), "Candidates missing from index audit")

    for mode in MODES:
        for key, value in summarize(rows, mode).items():
            for saved in (metrics["service_quality"], metrics["same_verified_subset"], analysis["metrics"]):
                close(value, saved[mode][key], f"Overall {mode}/{key}")
        for group in ("faq", "guide"):
            subset = [row for row in rows if ("faq" if row["expected_ids"][0].startswith("faq-") else "guide") == group]
            require(len(subset) == analysis["subgroups"][group][mode]["n"], "Subgroup size differs")
            for key, value in summarize(subset, mode).items():
                close(value, analysis["subgroups"][group][mode][key], f"Subgroup {group}/{mode}/{key}")

    for left, right in PAIRS:
        for key in KEYS:
            diff = [row["metrics"][right][key] - row["metrics"][left][key] for row in rows]
            saved = analysis["pairs"][f"{left}_to_{right}"][key]
            close(100 * math.fsum(diff) / len(rows), saved["delta_pp"], f"Paired change {key}")
            require(sum(value > 0 for value in diff) == saved["improved"], "Improvement count differs")
            require(sum(value < 0 for value in diff) == saved["regressed"], "Regression count differs")
    misses = [row["query_index"] for row in rows if any(not row["metrics"][mode]["hit5"] for mode in MODES)]
    require(misses == [row["query_index"] for row in analysis["top5_misses"]], "Top-5 miss coverage differs")

    for field in ("prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens"):
        total = sum(call["usage"][field] for call in calls)
        require(total == metrics["usage"][field] == analysis["usage"][field], f"Usage differs: {field}")
    require({call["usage"]["model"] for call in calls} == set(metrics["usage"]["models"]), "Model differs")
    require(metrics["usage"]["llm_calls"] == metrics["verified_reranks"] == 190, "Call count differs")
    for field in ("failed_llm_calls", "injected_llm_calls", "unknown_usage_calls"):
        require(metrics["usage"][field] == 0, f"Unexpected call status: {field}")

    for path, saved in analysis["latency"].items():
        values = [(row["timings_ms"]["hybrid"] + row["timings_ms"]["rerank_only"])
                  if path == "hybrid_plus_rerank" else row["timings_ms"][path] for row in rows]
        require(all(math.isfinite(value) and value >= 0 for value in values), "Invalid duration")
        for key, value in {"mean_ms": math.fsum(values) / len(values), "p50_ms": percentile(values, .5),
                           "p95_ms": percentile(values, .95), "max_ms": max(values)}.items():
            close(value, saved[key], f"Latency {path}/{key}")
    if args.bootstrap:
        verify_bootstrap(rows, analysis)
    print(f"Verified {len(manifest['files'])} evidence files, 190 queries, 190 complete reranks and 431,480 tokens.")
    for mode in MODES:
        result = summarize(rows, mode)
        print(f"{mode}: Hit@1={result['hit1']:.2%}, Hit@5={result['hit5']:.2%}, MRR@5={result['mrr5']:.2%}")


if __name__ == "__main__":
    main()
