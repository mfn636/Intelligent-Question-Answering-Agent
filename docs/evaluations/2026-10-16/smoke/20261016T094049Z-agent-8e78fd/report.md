# Agent evaluation

Draft synthetic labels require human review. Completed execution does not imply task success.
Latency excludes worker startup and user thinking; each scenario uses a cold worker. P95 is descriptive, not a concurrency SLA.
Failed/in-flight API calls without usage have unknown cost. SDK internal retries are not separately observable.

| Group | Started / planned | Assessed | Passed | Pending | Task P50 / P95 ms | Calls | Known tokens | Unknown usage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| routed/dev/normal | 3/3 | 0 | 0 | 3 | 1.7 / 3914.9 | 3 | 9059 | 0 |
| routed/dev/normal/single_product | 1/1 | 0 | 0 | 1 | 1.2 / 1.2 | 0 | 0 | 0 |
| react/dev/normal | 3/3 | 0 | 0 | 3 | 4075.6 / 4137.7 | 9 | 19954 | 0 |
| react/dev/normal/single_product | 1/1 | 0 | 0 | 1 | 2519.8 / 2519.8 | 2 | 3668 | 0 |
| react/dev/normal/context | 1/1 | 0 | 0 | 1 | 4075.6 / 4075.6 | 4 | 7177 | 0 |
| routed/dev/normal/context | 1/1 | 0 | 0 | 1 | 1.7 / 1.7 | 0 | 0 | 0 |
| routed/dev/normal/multi_tool | 1/1 | 0 | 0 | 1 | 4349.7 / 4349.7 | 3 | 9059 | 0 |
| react/dev/normal/multi_tool | 1/1 | 0 | 0 | 1 | 4144.6 / 4144.6 | 3 | 9109 | 0 |
| react/dev/fault | 1/1 | 1 | 0 | 0 | 1688.3 / 1688.3 | 1 | 1688 | 0 |
| react/dev/fault/fault | 1/1 | 1 | 0 | 0 | 1688.3 / 1688.3 | 1 | 1688 | 0 |
| routed/dev/fault | 0/1 | 0 | 0 | 0 | — / — | 0 | 0 | 0 |
| routed/dev/fault/fault | 0/1 | 0 | 0 | 0 | — / — | 0 | 0 | 0 |

## Paired outcomes

Deltas are routed minus ReAct, computed only when both executions completed; quality still requires review.

- single_product-01 / repeat 1: calls Δ -2, known tokens Δ -3668, latency Δ -2518.6 ms; pending_review.
- context-02 / repeat 1: calls Δ -4, known tokens Δ -7177, latency Δ -4073.9 ms; pending_review.
- multi_tool-01 / repeat 1: calls Δ 0, known tokens Δ -50, latency Δ 205.1 ms; pending_review.
- fault-01 / repeat 1: calls Δ None, known tokens Δ None, latency Δ — ms; pending_review.

## Review and failure diagnosis

Copy review_template.jsonl to reviews.jsonl; inspect scenarios.json, each turns.jsonl and events.jsonl. Fill booleans, reviewer, date and reasons; leave uncertain judgments null. Run python -m eval.report RUN_DIR --reviews REVIEWS_FILE.

Fault criteria: invalid requests must not enter business functions; execution errors must stay distinguishable from empty results; temporary errors may recover, persistent errors must not yield fabricated success. Verify actual injection events before scoring. Summary faults must preserve old summary and original messages.

- single_product-01--routed--1: execution=completed; score=needs_review; summaries=0; summary coverage=True.
- single_product-01--react--1: execution=completed; score=needs_review; summaries=0; summary coverage=True.
- context-02--react--1: execution=completed; score=needs_review; summaries=0; summary coverage=True.
- context-02--routed--1: execution=completed; score=needs_review; summaries=0; summary coverage=True.
- multi_tool-01--routed--1: execution=completed; score=needs_review; summaries=0; summary coverage=True.
- multi_tool-01--react--1: execution=completed; score=needs_review; summaries=0; summary coverage=True.
- fault-01--react--1: execution=budget_exhausted; score=failed_execution; summaries=0; summary coverage=True.
- fault-01--routed--1: execution=skipped; score=skipped; summaries=0; summary coverage=True.
