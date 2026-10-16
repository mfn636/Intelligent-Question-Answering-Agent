# Evaluation findings and follow-up experiments

This record separates measured results, reusable engineering practices and proposed changes. The [retrieval report](RAG_EVALUATION_2026-10-16.md) and [versioned evidence](evaluations/2026-10-16/README.md) are the source of the retrieval figures.

## What was actually verified

| Evaluation | Coverage | Result | Interpretation |
| --- | --- | --- | --- |
| Offline regression | 60 tests: 8 tools/bootstrap, 24 routing/workflows, 8 web, 20 evaluation-integrity | All passed | Deterministic behavior and evaluator safeguards; not model quality |
| Agent dataset validation | 100 draft scenarios | Structure and fixture fingerprints passed | Independent label review remains open |
| Real retrieval ablation | 190 queries × 3 configurations; 190 verified reranks | Hit@1: 77.89% → 77.89% → 83.68% | Complete retrieval experiment on a known development set |
| Agent routing smoke comparison | 3 normal scenarios × 2 configurations, one repeat | Simple product workflows avoided model requests | Semantic judgments remain pending; no full-Agent success rate |
| Focused fault smoke run | Invalid model JSON and temporary local execution error, each in 2 configurations | All 4 executions completed | Human review of recovery behavior remains pending |
| Controlled memory smoke run | One fixed 24-turn script, 24 field checks per strategy | Full 24/24, recent-10 9/24, summary 21/24 | Diagnostic extraction, not a general memory benchmark |

The first Agent smoke run also contains one budget-interrupted fault case and one unstarted case. They are preserved, not counted as successful executions. Its historical report predates the scorer fix and labels the interruption `failed_execution`; the correct interpretation is **evaluation incomplete**, not evidence of an Agent quality failure. The focused fault rerun is a separate run, not a replacement of the original evidence.

The full 100-scenario Agent quality evaluation, all 12 memory scripts, repeated model runs and concurrent load testing have not been completed. Early smoke runs also predate the small evaluation-assembly change that hides the knowledge tool when RAG is unavailable.

## Findings worth retaining

### 1. Candidate availability and ranking are different problems

All three retrieval configurations reached 100% target Hit@20, while Hit@1 ranged from 77.89% to 83.68%. For these queries, adding more candidates is not the first issue to address; the target already exists in the candidate set. Preserve candidate IDs and inspect ordering failures before increasing retrieval depth.

### 2. Hybrid retrieval needs subgroup evidence

Hybrid FAQ Hit@1 rose from 73.33% to 77.50%, while long-document Hit@1 fell from 85.71% to 78.57%. Overall Hit@1 stayed unchanged, and overall Hit@5 fell by 1.05 points. A larger retrieval architecture is not evidence of a better result. Keep FAQ and long-document comparisons alongside aggregate metrics.

### 3. Reranking has a measurable quality–latency tradeoff

Relative to hybrid, LLM reranking fixed 14 Top-1 misses and introduced 3 regressions, for a net gain of 11/190. It added an average 1,509.55 ms per query, with 190 requests consuming 431,480 tokens. Record improvements and regressions together with latency and total usage. This result alone does not justify enabling reranking for every production request.

### 4. Single-target labels can reject useful evidence

Two remaining reranked Top-5 misses contain relevant alternative results, including a different FAQ that explicitly answers the logistics question. Keep the original scores intact. Create a separately versioned, independently reviewed multi-relevance dataset; do not broaden labels after seeing a candidate configuration's output to improve its reported score.

### 5. Fixed workflows save calls on the tasks they cover

In the paired smoke run, price lookup changed from 2 model calls to 0 and from 2,519.8 ms to 1.2 ms. A two-turn inventory/price task changed from 4 calls to 0 and from 4,075.6 ms to 1.7 ms. The recommendation case still used 3 calls in both configurations and took 4,144.6 ms versus 4,349.7 ms. These are single-run observations with semantic review pending, not an overall speedup claim. Routing should be evaluated by task category and business correctness.

### 6. Summaries preserve context but are not automatically cheaper

The memory smoke run recovered 21/24 fields with summaries versus 9/24 with recent messages alone. Full history recovered 24/24. Total usage was 5,446 tokens for summaries, 1,364 for recent messages and 3,212 for full history; summary mode used 6 calls including 3 compactions, versus 3 calls for each baseline. Include summary overhead and check whether the conversation is long enough to amortize it before claiming token savings.

### 7. Reproducibility includes checkout behavior

Publication checks reproduced a false fixture-drift error when Windows CRLF files were checked out as LF. The dataset manifest now fingerprints LF-normalized text, while raw experiment snapshots retain byte hashes. A regression test validates both checkout styles and confirms that actual scenario or product changes still fail. This fixes portability of future evaluations without changing any recorded experiment result.

## Reusable evaluation practices

1. Freeze inputs, labels, source/index fingerprints, model settings and candidate limits before comparing variants.
2. Preserve per-query results, original score responses, usage and failures. A report should be recomputable from its evidence.
3. Observe success below fallback boundaries. A reranker returning a list does not establish that its model scores were valid.
4. Separate execution completion, structural correctness, semantic task success and grounding. Do not turn pending reviews into passes.
5. Count all requests, including summaries and failed attempts. Missing usage remains unknown. Compare combined-path percentiles from per-query totals.
6. Keep paired queries and target-document clusters together when estimating uncertainty. Preserve every repeat rather than selecting a favorable run.
7. Record the default configuration separately from experimental options. Keep old experiments as historical records when scope or fixtures change.

## Proposed work, in priority order

These are experiments or review tasks, not implemented improvements.

| Priority | Next step | Evidence to require before adoption |
| --- | --- | --- |
| 1 | Independently review ambiguous/overlapping knowledge labels and Agent pilot criteria; version changes | Reviewed relevant-document sets and explicit insufficient-evidence criteria, without rewriting this baseline |
| 2 | Diagnose long-document regressions under RRF, including repeated chunks from one document | Paired subgroup ranks and regressions on development data, followed by held-out validation |
| 3 | Compare a dedicated cross-encoder and selective reranking with the current LLM reranker | Same candidate sets; quality, fallback rate, latency and resource usage across repeats |
| 4 | Complete and review the Agent pilot, then the full paired Agent comparison | Business success, grounding, routing mistakes and coverage by category; retain budget/timeout outcomes |
| 5 | Run all memory scripts and realistic long Agent conversations | Constraint retention, stale values, unknown-field handling and total usage including summaries |
| 6 | Harden RAG dependencies and index lifecycle separately | Reproducible tests for retries/fallback, index replacement, embedding validation and cache freshness |

## A defensible project statement

Implemented a reproducible evaluation harness with fixture fingerprints, per-call traces, fault injection and human-review import. Evaluated vector, BM25/RRF hybrid and Top-20 LLM reranking on 190 knowledge queries: reranking raised Hit@1 from 77.89% to 83.68%, with 190 calls and 431,480 tokens; documented its approximately 1.51-second incremental latency, subgroup regressions and label limitations.

This statement describes implemented evaluation capabilities and observed retrieval results. It does not claim a full-Agent task success rate or production-scale performance.
