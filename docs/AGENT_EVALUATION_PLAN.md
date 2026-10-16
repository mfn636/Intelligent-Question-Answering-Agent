# Agent evaluation design

Designed on October 16, 2026 against baseline `ba90793`.

**Status:** the evaluation harness, 100 draft synthetic scenarios, observation wrappers, fault injection, review import and retrieval/memory diagnostics are implemented. The full 190-query retrieval run is complete. Agent paired smoke runs and one memory diagnostic have been executed; independent label review and full Agent quality evaluation remain open. This document describes the evaluation design, not achieved quality targets.

See [the usage guide](EVALUATION.md), [the retrieval report](RAG_EVALUATION_2026-10-16.md) and [findings and follow-up work](EVALUATION_FINDINGS_2026-10-16.md).

## Questions and evaluation boundaries

1. Does the system complete the user's task correctly?
2. Which layer explains a failure: routing, model arguments, execution, retrieval, memory or answer generation?
3. Does a change improve quality enough to justify its latency and resource cost?

Keep deterministic regression tests, real Agent quality evaluation and component diagnostics separate. At design time the repository had 40 functional/integration tests; 20 evaluation-integrity tests bring the current total to 60, including a cross-platform fingerprint regression added during publication. Passing these checks does not establish model quality.

The main evaluation entry point is `ConversationRunner.run()`. Observe `TurnResult`, `AgentState` and `RunTrace`, with evaluation-only wrappers for additional evidence. Web isolation and request deduplication remain integration-test concerns; web queuing and rendering are not included in core execution timing.

Current production paths are `direct`, `workflow` and `agent`. Four tools are exposed: `search_products`, `get_product_detail`, `check_inventory` and `search_knowledge`. Local functions own product/inventory data; RAG owns FAQ, guides, policies and help. Default RAG is hybrid without reranking. A five-step ReAct limit is not a five-call limit: steps may contain multiple tools, and routing, summaries and finalization may add model calls. Local retries and circuit breakers are not yet a complete production policy.

## Scenario design

Count task scenarios, not messages or API calls. Reuse one Runner within a scenario and isolate different scenarios.

| Category | Draft scenarios | Pilot | Coverage |
| --- | ---: | ---: | --- |
| Social, clarification and capability boundaries | 10 | 3 | Greetings, missing product IDs, ambiguous references, unsupported purchases/payments |
| Single-product queries | 20 | 8 | Price, inventory, specifications, absent products and missing attributes |
| Multi-turn context | 20 | 6 | References, changed budgets, excluded brands, cancellations, candidates versus decisions, summary boundaries |
| Multiple-tool tasks | 15 | 4 | Filter then check stock, compare products, explain tradeoffs from specifications |
| Knowledge questions | 20 | 5 | 12 answerable drafts and 8 proposed insufficient-evidence/no-answer cases requiring corpus review |
| Injected faults | 15 | 4 | Unknown tools, invalid JSON, type/extra-field errors, temporary/persistent failures, invalid results and empty retrieval |
| Total | 100 | 30 | Report 85 normal and 15 fault scenarios separately |

The context design includes 12 short and 8 long scenarios. Check actual summary events, including repeat compaction and superseded constraints; input length alone does not prove compaction coverage. Direct replies and workflows do not immediately trigger summarization.

Use a 60/40 development/provisional-acceptance split grouped by scenario family, with the pilot drawn from development. Template variants must not cross the split. Labels and the split require independent review before freezing. If acceptance examples guide subsequent tuning, obtain new held-out examples. This coverage mix is not an estimate of production traffic.

## Completion criteria and scoring

Store stable IDs/version/split/category, user turns, fixture versions, expected behavior, required facts, forbidden claims, allowed routes/tool plans, evidence targets and limits. Criteria must remain outside the evaluated Agent's context.

For `NB002 有货吗` followed by `它的价格`, the current fixture requires NB002 stock 0 and price 4,799 yuan. A different product's price or a claim of available inventory fails the business criteria. The routed configuration should use workflows without model requests, but the all-ReAct baseline can still succeed if its answer is correct.

For an in-stock laptop under 5,000 yuan, validate category, price and availability together. A correct tool name cannot excuse the wrong product or an over-budget recommendation. Correctly reporting no match is success when supported by the fixture.

| Measure | Interpretation |
| --- | --- |
| Task success | All required criteria met and no forbidden claim, among fully assessed normal scenarios; publish coverage |
| Subtask completion | Diagnostic coverage of required goals, not a replacement for task success |
| Tool-plan coverage | Allowed successful tool combinations and required argument subsets |
| Unnecessary calls | Tool requests in scenarios explicitly requiring none |
| Argument validity | Existing tool, parseable JSON object and acceptance by its Pydantic model |
| Argument semantics | Valid arguments also match the user's intended product, budget and constraints |
| Grounding | Business assertions follow user constraints, tools or permitted prior facts |
| Fault handling | Invalid requests never enter business functions; failures remain explicit; no fabricated success |
| Efficiency | Calls, tokens, failures, elapsed time and optional verified currency cost |

Free-form answers require human review of facts and evidence. Do not grade by whole-answer string matching or number occurrence. Future model-assisted judging needs calibration against human judgments and recorded disagreement handling.

## Observation and fault attribution

Record original model arguments, stage, parameters, response/error, actual model ID, usage and timing. Distinguish routing, summary, executor and finalizer calls. Mark whether tool validation passed and whether the business function actually started. Capture retrieval IDs and parent IDs below the tool serialization layer, which removes those IDs from model-facing results.

Retain timings and usage for failed turns as well as successful ones. Distinguish actual reranking from silent fallback: unchanged order alone proves neither success nor failure. Fault injection happens only in evaluation wrappers and is tagged separately from naturally occurring faults. Synthetic faults do not estimate real failure frequency.

## Retrieval comparison

Use the existing 190 knowledge queries from the 270-query dataset; exclude 80 product queries. Audit source chunks against the index and verify target existence. Preserve original labels.

| Variant | Fixed conditions |
| --- | --- |
| Vector | Existing Qdrant index and BGE-M3 query encoder, 20 candidates, existing threshold/fallback |
| Hybrid | Vector 20 + BM25 20, RRF k=60, retain 20 |
| Hybrid + reranking | Exactly the same hybrid candidates, explicit reranker/model/parameters/snippet length |

Set `LLMReranker(max_cands=20)` explicitly; its production class default is 10. Report Hit@1/5/20 and MRR@5 for single designated targets. Exhaustive recall and graded nDCG need richer relevance labels. Version and independently review new labels before future comparisons rather than modifying them after inspecting ranks.

Keep failed reranks in actual service results. A successful-subset analysis must compare all configurations on the same subset. Preserve correlations between paraphrases of the same target in splitting and uncertainty estimates. These old queries are known development data, not a new unseen test set. Retrieval success and final-answer success remain different outcomes.

## Memory and routing comparisons

Use the existing 12 fixed 24-turn transcripts for controlled `full / recent10 / summary` diagnostics. Track changed constraints, unknown fields, user decisions versus assistant suggestions, summary errors and original-history retention. Include every summary call in total usage. Synthetic extraction IDs are not product-tool requests; separately evaluate realistic multi-turn Agent tasks.

Compare all-ReAct against current routing + fixed workflows + ReAct with shared fixtures, prompts, model and memory. Interleave paired variants, retain repeats and report results by category so simple queries do not hide regressions on complex tasks. Change one meaningful factor at a time.

## Evidence, budgets and acceptance

Freeze code/data hashes, scenario labels, model parameters, tool schemas, index audit and environment. Save each run independently and persist progress per turn/call. Isolated workers bound runtime; terminated requests can have unknown provider-side consumption. Record skipped, budget-interrupted, timed-out and failed cases distinctly.

Start serially because the core shares model usage and lazy resources. Report cold initialization separately where measured; do not present descriptive P95 as a concurrency guarantee. For formal Agent comparisons, plan three repeats per configuration and retain every result. Bootstrap units should respect scenario families or target-document clusters.

Currency estimates require dated model-specific prices. Cached input is a subset of input; unknown usage is never free. Report all consumed resources, including failures and summaries. Scoring overhead is separate from serving cost.

Implementation is under `eval/`, with draft cases under `data/eval/` and ignored raw runs under `eval/results/`. Publish selected evidence snapshots under `docs/evaluations/` for independent inspection. Raw credentials and browser cookies are not part of the evidence format.

The acceptance sequence is: review pilot criteria, verify observation and budgets, run and review the pilot, freeze independently checked labels, execute full paired runs, then prioritize changes by measured failures. Initial acceptance concerns reproducibility, traceability and failure attribution. Quality, latency and cost thresholds follow measured baselines; they are not invented in advance.
