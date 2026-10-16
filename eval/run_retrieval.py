"""Knowledge-only vector / hybrid / verified Top-20 LLM reranking ablation."""
import argparse
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import json
import math
import time

from eval.common import (ROOT, BudgetExceeded, append_jsonl, file_hash, new_run, read_json,
                         read_jsonl, safe_error, supervised, write_json)
from eval.recorders import ObservedLLM, Recorder
from eval.runtime import audit_index, open_retriever
from eval.scoring import usage_totals


def ranking_metrics(items, expected_ids):
    # Match either chunk ID or a parent document ID. FAQ IDs match directly.
    ranks = [i for i, item in enumerate(items, 1)
             if {item["id"], item["payload"].get("doc_id")} & set(expected_ids)]
    first = min(ranks, default=math.inf)
    return {"hit1": int(first <= 1), "hit5": int(first <= 5), "hit20": int(first <= 20), "mrr5": 1 / first if first <= 5 else 0}


def valid_scores(content, ids):
    try:
        values = json.loads(content)["scores"]
        returned = [entry["id"] for entry in values]
        if len(returned) != len(set(returned)) or set(returned) != set(ids):
            return False
        return all(type(entry["score"]) in (int, float) and math.isfinite(entry["score"]) and 0 <= entry["score"] <= 1 for entry in values)
    except (KeyError, TypeError, ValueError):
        return False


class ScoreObserver:
    """Observe the existing reranker response without hiding its real fallback behavior."""
    def __init__(self, llm):
        self.llm, self.ids, self.valid, self.reason = llm, [], False, "not_called"

    def chat(self, **kwargs):
        try:
            response = self.llm.chat(**kwargs)
        except Exception:
            self.reason = "llm_error_fallback"
            raise
        self.valid = valid_scores(response.content, self.ids)
        self.reason = "verified" if self.valid else "invalid_or_incomplete_scores"
        return response


def retrieval_worker(payload, directory):
    from bootstrap import _build_llm
    from rag.collections import COLLECTION_KNOWLEDGE
    from rag.rerank import LLMReranker
    path = Path(directory)
    recorder = Recorder(path, max_calls=payload["max_calls"], max_tokens=payload["max_tokens"])
    status = {"status": "completed"}
    started_queries = False
    try:
        with ExitStack() as resources:
            retriever = open_retriever(resources)
            audit = audit_index(retriever)
            write_json(path / "index_audit.json", audit)
            available = set(audit["ids"])
            # Include parent IDs in label existence audit.
            from rag.ingest import build_collection_specs
            available.update(c["payload"].get("doc_id") for c in build_collection_specs()[0].build_chunks())
            for query in payload["queries"]:
                if not set(query["expected_ids"]) <= available:
                    raise ValueError("Golden label missing from current index: " + str(query["expected_ids"]))
            score_observer = ScoreObserver(ObservedLLM(_build_llm(resources), recorder, stage="rerank"))
            reranker = LLMReranker(score_observer, max_cands=20, snippet=180)
            for number, query in enumerate(payload["queries"], 1):
                started_queries = True
                recorder.turn = number
                recorder.emit("turn_start", query=query["query"])
                results, timings = {}, {}
                for mode in ("vector", "hybrid"):
                    started = time.perf_counter()
                    results[mode] = retriever.retrieve(COLLECTION_KNOWLEDGE, query["query"], top_k=20, mode=mode, rerank=False)
                    timings[mode] = (time.perf_counter() - started) * 1000
                    recorder.emit("retrieval_ranking", mode=mode, items=results[mode], elapsed_ms=timings[mode])
                rerank_status = "disabled"
                budget_error = None
                if payload["reranker"] == "llm":
                    score_observer.ids = [item["id"] for item in results["hybrid"]]
                    score_observer.valid, score_observer.reason = False, "not_called"
                    started = time.perf_counter()
                    try:
                        results["hybrid_rerank"] = reranker.rerank(query["query"], deepcopy(results["hybrid"]), top_k=20)
                    except BudgetExceeded as exc:
                        budget_error = exc
                    timings["rerank_only"] = (time.perf_counter() - started) * 1000
                    rerank_status = "budget_exhausted" if budget_error else score_observer.reason
                    recorder.emit("retrieval_ranking", mode="hybrid_rerank", items=results.get("hybrid_rerank"), status=rerank_status,
                                  elapsed_ms=timings["rerank_only"])
                append_jsonl(path / "queries.jsonl", {"query_index": number, **query, "results": results,
                             "timings_ms": timings, "rerank_status": rerank_status,
                             "metrics": {k: ranking_metrics(v, query["expected_ids"]) for k, v in results.items()}})
                recorder.emit("turn_end", elapsed_ms=sum(timings.values()))
                if budget_error:
                    raise budget_error
    except (Exception, BudgetExceeded) as exc:
        status = {"status": "budget_exhausted" if isinstance(exc, BudgetExceeded) else "error" if started_queries else "unavailable", "error": safe_error(exc)}
    finally:
        write_json(path / "status.json", status)


def summarize_queries(rows, *, verified_only=False):
    rows = [r for r in rows if not verified_only or r["rerank_status"] == "verified"]
    output = {}
    for mode in ("vector", "hybrid", "hybrid_rerank"):
        values = [r["metrics"][mode] for r in rows if mode in r["metrics"]]
        output[mode] = {"n": len(values), **{k: sum(v[k] for v in values) / len(values) if values else None for k in ("hit1", "hit5", "hit20", "mrr5")}}
    return output


def paired_gains(rows):
    gains = {}
    for a, b in (("vector", "hybrid"), ("hybrid", "hybrid_rerank"), ("vector", "hybrid_rerank")):
        same = [r for r in rows if a in r["metrics"] and b in r["metrics"]]
        gains[a + "_to_" + b] = {"n": len(same), **{
            key + "_pp": 100 * sum(r["metrics"][b][key] - r["metrics"][a][key] for r in same) / len(same) if same else None
            for key in ("hit1", "hit5", "hit20", "mrr5")}}
    return gains


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=190)
    parser.add_argument("--reranker", choices=["none", "llm"], default="none")
    parser.add_argument("--max-calls", type=int, default=200)
    parser.add_argument("--max-tokens", type=int, default=250000)
    parser.add_argument("--query-timeout", type=float, default=120)
    parser.add_argument("--total-timeout", type=float, default=3600)
    args = parser.parse_args(argv)
    if min(args.limit, args.max_calls, args.max_tokens, args.query_timeout, args.total_timeout) <= 0:
        parser.error("Positive limits required")
    source = ROOT / "data/retrieval_golden.json"
    queries = [row for row in read_json(source) if all(not item.startswith("product-") for item in row["expected_ids"])][:args.limit]
    directory = new_run("retrieval", {**vars(args), "dataset_sha256": file_hash(source), "queries": len(queries),
                                       "scope": "Known development set; one target document per query; no exhaustive relevance labels",
                                       "recall_n": 20, "rrf_k": 60, "score_threshold": .3,
                                       "reranker_config": {"kind": args.reranker, "max_cands": 20, "snippet": 180}})
    write_json(directory / "queries.json", queries)
    status = supervised(retrieval_worker, {**vars(args), "queries": queries}, directory / "worker",
                        case_timeout=args.total_timeout, turn_timeout=args.query_timeout)
    rows = read_jsonl(directory / "worker/queries.jsonl")
    metrics = {"status": status, "planned": len(queries), "completed": len(rows),
               "service_quality": summarize_queries(rows), "same_verified_subset": summarize_queries(rows, verified_only=True),
               "verified_reranks": sum(r["rerank_status"] == "verified" for r in rows),
               "paired_gains": paired_gains(rows),
               "usage": usage_totals(read_jsonl(directory / "worker/events.jsonl"))}
    write_json(directory / "metrics.json", metrics)
    lines = ["# Knowledge retrieval evaluation", "", f"Completed {len(rows)}/{len(queries)} queries. Status: {status['status']}.",
             "Single-target Hit@K, not exhaustive evidence Recall@K. Products are excluded. The reranker receives exactly the hybrid candidates.",
             "Incomplete/invalid rerank scores stay in service results and are excluded from the shared verified subset. Rerank-only timing is separate from candidate retrieval.", ""]
    for title in ("service_quality", "same_verified_subset"):
        lines += ["## " + title, "", "| Mode | n | Hit@1 | Hit@5 | Hit@20 | MRR@5 |", "| --- | --- | --- | --- | --- | --- |"]
        for mode, values in metrics[title].items():
            numbers = ["—" if values[k] is None else f"{values[k]:.3%}" for k in ("hit1", "hit5", "hit20", "mrr5")]
            lines.append(f"| {mode} | {values['n']} | " + " | ".join(numbers) + " |")
        lines.append("")
    lines += ["## Paired gains (percentage points)", "", "Each pair uses queries completed by both configurations; failures/fallbacks remain in service quality.", ""]
    for pair, values in metrics["paired_gains"].items():
        values_text = ", ".join(f"{key}: {'unavailable' if values[key] is None else round(values[key], 3)}" for key in ("hit1_pp", "hit5_pp", "hit20_pp", "mrr5_pp"))
        lines.append(f"- {pair}, n={values['n']}: {values_text}.")
    if status.get("error"):
        lines.append("Dependency/execution error: " + str(status["error"]))
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(directory / "report.md")


if __name__ == "__main__":
    main()
