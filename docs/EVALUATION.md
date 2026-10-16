# Evaluation guide

The evaluation package measures real Agent scenarios, paired routing configurations, knowledge retrieval and controlled conversation memory. Instrumentation lives in `eval/`; it does not change production routing, prompts, argument validation or error handling.

The Agent dataset contains **100 draft synthetic scenarios**, including a 30-scenario pilot. Its 60 development / 40 provisional acceptance split is grouped by scenario family. Labels, especially no-answer labels, still need independent human review. It is not a frozen golden set or an independently validated test set.

The completed retrieval baseline is documented in [the October 16 report](RAG_EVALUATION_2026-10-16.md). Public evidence and offline verification are available in [the evidence bundle](evaluations/2026-10-16/README.md).

## Quick start

Run from the repository root. Agent evaluation calls the real `ConversationRunner.run()` without the web server.

```bash
# Validate scenarios and fixture fingerprints without model requests
python -m eval.run_agent --validate-only --suite all

# Deterministic greetings, price lookup and a two-turn reference
python -m eval.run_agent --rag skip --ids social-01 single_product-01 context-02

# Small paired run with the configured model and local tools
python -m eval.run_agent --rag skip --ids single_product-01 context-02 --variants routed react --max-calls 12 --max-tokens 20000

# Pilot: inspect coverage, usage and pending reviews before scaling up
python -m eval.run_agent --suite pilot --variants routed react --max-calls 200 --max-tokens 300000

# After independent label review: full paired runs with repeats
python -m eval.run_agent --suite all --variants routed react --repeats 3 --max-calls 2000 --max-tokens 2000000
```

Live runs use the API configured in `.env`. Budgets bound execution; they are not price estimates. Default timeouts are 90 seconds per turn and 600 seconds per scenario, configurable with `--turn-timeout` and `--case-timeout`. Long scenarios can exhaust a small budget.

`routed` uses the current entry router. `react` disables it only in evaluation assembly. Both use the same business fixtures, tool definitions, model settings, prompts and memory policy. Paired executions are interleaved and all repeats are retained. Using ReAct rather than a fixed workflow is not itself a task failure.

## Evidence and human review

Each run creates a fresh, Git-ignored `eval/results/<run_id>/` directory.

| File | Contents |
| --- | --- |
| `config.json` | Code and data hashes, baseline commit, environment, configuration and date |
| `scenarios.json` | Frozen scenarios and grading criteria, visible only to the scorer |
| `schedule.json` | Planned scenarios, variants and repeats |
| `dependencies.json` | Retrieval preflight status and reasons for unavailable dependencies |
| `cases.jsonl` | Execution status, including skips, budget interruptions and timeouts |
| `<case--variant--repeat>/turns.jsonl` | Answers, state, history, errors and outer execution timings |
| `<case--variant--repeat>/events.jsonl` | Model stages, arguments, function entry, failures, retrieval IDs and summaries |
| `review_template.jsonl` | Blank review sheet; semantic judgments default to `null` |
| `metrics.json`, `report.md` | Grouped metrics, paired differences, failures and coverage |

**`completed` means execution finished; it does not mean the task was answered correctly.** A price appearing somewhere in an answer does not prove it belongs to the requested product. A successful tool call does not prove that the answer follows the evidence.

1. Copy `review_template.jsonl` to `reviews.jsonl`.
2. Inspect `scenarios.json`, `turns.jsonl` and `events.jsonl`. Fill each turn's `criteria`, checking product–attribute–value relationships, constraints, references, clarification and unknown information.
3. Fill `grounded`, `forbidden_claims_absent` and `argument_semantics_correct`. Accept evidence from user constraints, tool results or permitted prior facts; model guesses are not evidence.
4. For injected faults, apply `fault_expectation` and fill `fault_handling`. Persistent dependency failures should be exposed without fabricated success. An injection that never happened is `fault_not_exercised`.
5. Record `reviewer`, `reviewed_at` and `notes`. Leave uncertain judgments `null`.
6. Import the review:

```bash
python -m eval.report eval/results/RUN_ID --reviews eval/results/RUN_ID/reviews.jsonl
```

Review import checks scenario/evidence hashes, fields and boolean values to prevent stale reviews from being attached to a different run. Regenerating derived reports does not replace raw evidence or human review files.

Reports separate normal tasks from injected faults and show planned, started, completed, assessed and pending counts. Success rates use fully assessed cases and display coverage. Budget interruptions are incomplete evaluation, not Agent quality failures. Timeouts and execution errors remain visible, with their elapsed time and known usage.

Structural checks cover permitted tool plans, unnecessary calls, argument validity, error stages and summary triggers. Matching a tool name and an argument subset does not establish business correctness. Argument semantics currently require scenario-level review.

## Budgets, timing and cost

- `--max-calls` counts started model-wrapper calls across routing, summarization, execution and finalization. Faults injected before HTTP are marked separately. SDK-internal HTTP retries are not independently counted.
- `--max-tokens` checks returned usage before the next request. A response can push the total past the threshold; this is not a provider-side billing cap. Failed or terminated requests without usage have **unknown consumption**, not zero cost.
- Scenarios run in isolated subprocesses. Timeout or interruption terminates the active worker; requests already received by the provider may still complete and incur charges. Incremental evidence retains work completed before interruption.
- P50/P95 use linear interpolation. Turn timing includes evaluation recording, but excludes worker startup, web queuing and user thinking. Each scenario starts a cold worker; later turns reuse its resources. Worker duration is recorded separately. This is not concurrent load testing.
- `usage.stages` separates routing, summarization, execution and finalization. Cached input tokens are a subset of total input tokens and must not be added twice.
- Prices are not embedded. `eval.report --prices prices.json` accepts `model`, `currency`, `date`, `source`, `uncached_input_per_million`, `cached_input_per_million` and `output_per_million`. Supply verifiable prices for the matching model. Currency totals cover known usage only; unknown requests remain separate.

## Retrieval ablation

```bash
# Check the existing index and services without paid reranking
python -m eval.run_retrieval --limit 5

# Reproduce the full-run configuration; actual model calls incur usage
python -m eval.run_retrieval --limit 190 --reranker llm --max-calls 190 --max-tokens 2000000 --query-timeout 120 --total-timeout 3600
```

This uses all **190 knowledge queries** from the original 270-query dataset; the 80 product queries are outside current RAG scope. Original queries and labels are unchanged. Each query has one designated target, so the reported metrics are `Hit@1`, `Hit@5`, `Hit@20` and `MRR@5`, not exhaustive relevance recall.

Preflight pages through the Qdrant knowledge collection and compares chunk IDs, text and metadata with current source chunks. Missing targets, stale content or unavailable dependencies prevent a valid score. Evaluation does not delete or rebuild the index. The old index lacks embedding-model provenance; recording today's BGE-M3 settings does not establish which version produced historical vectors.

Vector and hybrid paths use the existing retriever. The same hybrid Top-20 candidates feed `LLMReranker(max_cands=20, snippet=180)`. Observers verify complete score coverage, unique IDs and finite scores in [0, 1] without changing the production fallback behavior.

Reports show actual service outcomes including fallback and an A/B/C comparison restricted to the **same successfully verified query subset**. Reranking is opt-in via `--reranker llm`. IDs, parent-document IDs, text, timings and model usage are retained separately from Agent evaluation.

Requirements: `qdrant-client`, a populated local knowledge index and Ollama serving `bge-m3`. Avoid simultaneous processes opening the local Qdrant database; schedule evaluation separately from web knowledge queries.

## Controlled memory diagnostic

```bash
python -m eval.run_memory --limit 1 --max-calls 15 --max-tokens 30000
python -m eval.run_memory --limit 12 --max-calls 180 --max-tokens 250000
```

Compare `full`, `recent10` and `summary` on identical, prewritten user/assistant transcripts. Checkpoint answers are not inserted back into history. The summary condition invokes production `ConversationHistory.compact()` and `ConversationSummarizer`; failure checks verify preservation of prior summaries and original messages. Both summary and extraction requests count toward usage.

Field checks use explicit values/aliases or numeric equality, not numeric substring matching. Missing fields and invalid JSON do not count as correct unknown values. Reports separate invented unknowns, stale values, summary failures and field coverage. Synthetic candidate IDs are not real products, so this diagnostic does not call product tools or establish end-to-end shopping success.

## Dataset maintenance and implementation

`data/eval/manifest.json` fingerprints scenarios and product, inventory and knowledge fixtures. Changed fixtures invalidate old labels. Review changes and version the dataset before comparison. `python -m eval.build_cases` **overwrites the generated draft**; do not use it to replace human-reviewed labels. Retain separately versioned formal datasets.

The current dataset manifest uses `hash_format: sha256-lf`: CRLF line endings are normalized to LF before hashing, so Git checkout conventions do not cause false fixture drift. Content changes still fail validation. Raw run snapshots continue to use byte hashes for provenance. The October 16 benchmark and early smoke files retain their original fingerprints; this portability fix was added afterward and did not change their labels or scores.

| File under `eval/` | Responsibility |
| --- | --- |
| `schema.py`, `build_cases.py` | Scenario structures and draft generation from sample fixtures |
| `common.py` | Fingerprints, incremental persistence, subprocesses and timeouts |
| `runtime.py` | Evaluation assembly, index audit and real Runner execution |
| `recorders.py` | Model/tool/retrieval/summary observation and fault injection |
| `run_agent.py` | Selection, paired scheduling and run budgets |
| `scoring.py`, `report.py` | Structural checks, review import, metrics and reports |
| `run_retrieval.py`, `run_memory.py` | Focused diagnostics |

```bash
python -m unittest discover -s tests -v
```

There is no automatic LLM judge, concurrent load benchmark or evaluation dashboard. Agent-wide paired confidence intervals remain future work; the October 16 retrieval evidence includes a separate document-cluster bootstrap analysis. Establish quality thresholds after independent review and complete baseline runs.
