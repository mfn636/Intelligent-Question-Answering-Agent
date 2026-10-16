# Knowledge retrieval benchmark — October 16, 2026

Run: `20261016T114913Z-retrieval-4c7925`. Baseline commit: `ba90793`; the then-uncommitted evaluation implementation is identified by per-file hashes in the run configuration.

**All 190 queries completed, and all 190 reranking responses passed validation.** There were no failed queries, skipped queries or missing model-usage records. Worker wall time was 305.704 seconds. The run used real local Qdrant retrieval, Ollama BGE-M3 query embeddings, BM25/RRF and DeepSeek LLM reranking.

Hybrid retrieval alone did not improve overall Hit@1 and reduced Hit@5 by 1.05 percentage points. Hybrid plus reranking improved Hit@1 by 5.79 points over vector search and Hit@5 by 0.53 points, while adding approximately 1.51 seconds and one model request per query relative to hybrid retrieval.

## Scope and configuration

- Dataset: all 190 knowledge queries from `data/retrieval_golden.json`. The original 80 product queries are excluded because products are no longer managed by RAG. Queries and labels were not changed.
- Coverage: 95 target documents, each with two distinct queries; 120 FAQ queries and 70 guide/policy/help queries. All 190 query texts are unique.
- Index: 536 chunks, 1,024-dimensional vectors, Cosine distance. Chunk IDs, text and metadata match current source chunks; all designated targets exist.
- Vector: 20 candidates, existing `score_threshold=0.3` and fallback behavior.
- Hybrid: vector 20 + BM25 20, RRF k=60, retain 20. BM25 is the repository's local implementation.
- Reranking: the exact same hybrid 20 candidates, `LLMReranker(max_cands=20, snippet=180)`, `deepseek-v4-flash`, temperature 0, maximum 800 output tokens, thinking disabled, JSON output.
- Environment: Python 3.12.7, qdrant-client 1.19.1, openai 2.44.0, pydantic 2.14.1, requests 2.34.2. Full fingerprints are preserved in the evidence bundle.
- This was one complete run. Production Agent and retrieval algorithms were not changed, and the index was not rebuilt. **The default Agent still uses hybrid retrieval without reranking.**

```bash
python -m eval.run_retrieval --limit 190 --reranker llm --max-calls 190 --max-tokens 2000000 --query-timeout 120 --total-timeout 3600
```

## Overall results

Each query has one designated target. A result matches by chunk ID or parent `doc_id`; FAQ targets use their chunk IDs. Hit@K measures whether that target appears within K results. MRR@5 averages the reciprocal first matching rank within the first five results, with zero otherwise. These are retrieval metrics, not final-answer accuracy or exhaustive relevance recall.

| Configuration | Hit@1 | Hit@5 | Hit@20 | MRR@5 |
| --- | ---: | ---: | ---: | ---: |
| Vector | 77.89% (148/190) | 98.42% (187/190) | 100% | 86.49% |
| Vector + BM25, RRF | 77.89% (148/190) | 97.37% (185/190) | 100% | 86.04% |
| Hybrid + Top-20 LLM reranking | 83.68% (159/190) | 98.95% (188/190) | 100% | 90.08% |

| Comparison | Hit@1 change | Hit@5 change | MRR@5 change |
| --- | ---: | ---: | ---: |
| Hybrid − vector | 0.00 pp | −1.05 pp | −0.45 pp |
| Hybrid + reranking − hybrid | +5.79 pp | +1.58 pp | +4.04 pp |
| Hybrid + reranking − vector | +5.79 pp | +0.53 pp | +3.59 pp |

Every configuration already retrieved the designated target within Top-20. The measured differences are therefore primarily ranking differences. This experiment does not support a claim that hybrid retrieval universally improves recall.

Compared with hybrid, reranking fixed 14 Top-1 misses and introduced 3 Top-1 regressions, a net gain of 11. It fixed 3 Top-5 misses without introducing another. Compared with vector search, it fixed 18 and regressed 7 Top-1 outcomes, and fixed 2 and regressed 1 Top-5 outcomes.

## Results by corpus group

| Group | Configuration | Hit@1 | Hit@5 | MRR@5 |
| --- | --- | ---: | ---: | ---: |
| FAQ (120 queries) | Vector | 73.33% | 98.33% | 83.89% |
| FAQ (120 queries) | Hybrid | 77.50% | 95.83% | 85.97% |
| FAQ (120 queries) | Hybrid + reranking | 83.33% | 98.33% | 90.14% |
| Guides/policies/help (70 queries) | Vector | 85.71% | 98.57% | 90.95% |
| Guides/policies/help (70 queries) | Hybrid | 78.57% | 100.00% | 86.17% |
| Guides/policies/help (70 queries) | Hybrid + reranking | 84.29% | 100.00% | 89.98% |

Hybrid improved FAQ Top-1 retrieval but reduced it for longer documents, cancelling out in the aggregate. Even after reranking, long-document Hit@1 remained slightly below vector search. Future changes should preserve these subgroup comparisons.

## Latency and model usage

| Path | Mean ms | P50 ms | P95 ms |
| --- | ---: | ---: | ---: |
| Vector | 42.60 | 38.55 | 75.71 |
| Hybrid | 49.81 | 42.89 | 87.81 |
| Reranking only | 1,509.55 | 1,490.03 | 1,806.82 |
| Hybrid + reranking | 1,559.36 | 1,532.85 | 1,855.41 |

Combined-path percentiles were computed from each query's hybrid time plus reranking time; percentiles were not added together. Percentiles use linear interpolation.

These are local serial measurements, with vector, hybrid and reranking executed in that order for each query. Recording overhead, warmup and caching may affect timings. They are not a concurrent service SLA or a randomized performance experiment. Total worker duration also includes both retrieval paths and evaluation setup.

| Usage | Measured value |
| --- | ---: |
| Reranking model calls | 190 |
| Failed model calls | 0 |
| Input tokens | 369,508 |
| Cached input tokens, included above | 7,936 |
| Output tokens | 61,972 |
| Total tokens | 431,480 |
| Calls with unknown usage | 0 |

Cached input is not counted twice. These totals cover reranking, not local embedding compute. No verified price table was configured, so no currency estimate is claimed. SDK-internal HTTP retries are not independently counted.

## Misses, regressions and label limitations

All six queries that missed the designated target in Top-5 under at least one configuration are listed below. Ranks are one-based.

| Query index | Original query | Target | Vector rank | Hybrid rank | Reranked rank |
| --- | --- | --- | ---: | ---: | ---: |
| 16 | 收到货后不满意能退吗？ | `faq-7` | 6 | 12 | 1 |
| 82 | 商品损坏了可以退换吗？ | `faq-25` | 3 | 13 | 7 |
| 86 | 在哪里可以查询订单物流信息？ | `faq-27` | 10 | 16 | 8 |
| 104 | 哪些人能使用分期付款？ | `faq-36` | 3 | 8 | 1 |
| 118 | 运费是按什么标准收取的？ | `faq-43` | 1 | 7 | 2 |
| 168 | 我的订单发货了吗，在哪里看物流进度？ | `help-帮助-2` | 6 | 4 | 3 |

Queries 82 and 86 still missed the designated target in Top-5 after reranking. Their leading results contain relevant alternatives, so this does not establish that no usable evidence was retrieved. For query 86, the first three results include a logistics guide, order-query steps and `faq-76`, which explicitly describes viewing logistics in “My orders”; the original label accepts only `faq-27`.

The three Top-1 regressions relative to hybrid are query 84 (cancelling an already shipped order), query 120 (free-shipping threshold) and query 130 (changing the address before shipment). All results are retained; no failed samples were removed and no best-of-many run was selected.

Labels were not widened after observing rankings. Independently review multiple relevant documents in a new dataset version before another comparison. Overlapping FAQ and policy/help content also warrants a separate consistency review.

## Descriptive uncertainty

Paired cluster bootstrap resampled 95 target-document groups while preserving each target's two queries: 10,000 resamples, seed 20261016.

| Comparison | Hit@1 change | 95% interval, pp | Hit@5 change | 95% interval, pp |
| --- | ---: | --- | ---: | --- |
| Hybrid − vector | 0.00 pp | [−5.26, +5.26] | −1.05 pp | [−3.16, +1.05] |
| Hybrid + reranking − hybrid | +5.79 pp | [+1.58, +10.00] | +1.58 pp | [0.00, +3.68] |
| Hybrid + reranking − vector | +5.79 pp | [+0.53, +11.05] | +0.53 pp | [−1.05, +2.63] |

These intervals describe resampling uncertainty within this known development set. They do not cover model-run variability, incomplete labels or production distribution shifts. Small Hit@5 differences should not be promoted as certain online gains.

## Evidence and interpretation boundaries

The [committed evidence bundle](evaluations/2026-10-16/README.md) contains configuration, frozen queries, index audit, all per-query rankings/timings, deduplicated candidate text, original reranker response strings, usage, aggregate analysis and SHA-256 manifests. Its offline verifier recomputes metrics and validates candidate/score coverage without paid model calls. Optional bootstrap verification reproduces the reported intervals.

Full original logs remain locally under the Git-ignored `eval/results/20261016T114913Z-retrieval-4c7925/`. Public evidence preserves information needed to inspect rankings and scores without repeatedly storing identical corpus text or entire request logs.

Integrity checks confirmed 190 sequential unique records, unchanged original labels, 20 candidates in every configuration, identical candidate sets before/after reranking and 190 responses with 20 unique finite scores in [0, 1]. All reranks succeeded, so service-quality and same-verified-subset results are identical in this run. Code and data hashes were checked against the run snapshot.

The existing index does not record historical embedding-model provenance. This report records the current BGE-M3 configuration, not proof of the model version that created historical vectors. Scope, corpus and reranker configuration differ from the earlier 270-query experiment, so absolute scores from those experiments are not directly comparable.

Next experiments and reusable evaluation practices are documented in [evaluation findings](EVALUATION_FINDINGS_2026-10-16.md). None of those proposed retrieval changes was implemented as part of this benchmark.
