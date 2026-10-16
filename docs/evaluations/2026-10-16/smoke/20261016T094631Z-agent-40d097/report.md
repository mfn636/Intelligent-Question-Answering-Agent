# Agent evaluation

Draft synthetic labels require human review. Completed execution does not imply task success.
Latency excludes worker startup and user thinking; each scenario uses a cold worker. P95 is descriptive, not a concurrency SLA.
Failed/in-flight API calls without usage have unknown cost. SDK internal retries are not separately observable. Budget-truncated cases remain unassessed; they are not Agent quality failures.

| Group | Started / planned | Assessed | Passed | Pending | Task P50 / P95 ms | Calls | Known tokens | Unknown usage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| routed/dev/fault | 2/2 | 0 | 0 | 2 | 3981.6 / 4031.0 | 6 | 11369 | 0 |
| routed/dev/fault/fault | 2/2 | 0 | 0 | 2 | 3981.6 / 4031.0 | 6 | 11369 | 0 |
| react/dev/fault | 2/2 | 0 | 0 | 2 | 4603.7 / 4857.0 | 6 | 11665 | 0 |
| react/dev/fault/fault | 2/2 | 0 | 0 | 2 | 4603.7 / 4857.0 | 6 | 11665 | 0 |

## Paired outcomes

Deltas are routed minus ReAct, computed only when both executions completed; quality still requires review.

- fault-01 / repeat 1: calls Δ 0, known tokens Δ -250, latency Δ -958.4 ms; pending_review.
- fault-03 / repeat 1: calls Δ 0, known tokens Δ -46, latency Δ -285.8 ms; pending_review.

## Review and failure diagnosis

Copy review_template.jsonl to reviews.jsonl; inspect scenarios.json, each turns.jsonl and events.jsonl. Fill booleans, reviewer, date and reasons; leave uncertain judgments null. Run python -m eval.report RUN_DIR --reviews REVIEWS_FILE.

Fault criteria: invalid requests must not enter business functions; execution errors must stay distinguishable from empty results; temporary errors may recover, persistent errors must not yield fabricated success. Verify actual injection events before scoring. Summary faults must preserve old summary and original messages.

- fault-01--routed--1: execution=completed; score=needs_review; summaries=0; summary coverage=True.
- fault-01--react--1: execution=completed; score=needs_review; summaries=0; summary coverage=True.
- fault-03--react--1: execution=completed; score=needs_review; summaries=0; summary coverage=True.
- fault-03--routed--1: execution=completed; score=needs_review; summaries=0; summary coverage=True.
