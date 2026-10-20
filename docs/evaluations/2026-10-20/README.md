# Agent Safety and Boundary Evaluation

## Scope

This evaluation runs the 10 manually designed behavior cases in
`data/eval/agent_golden_v1.jsonl` against two variants:

- `routed`: entry intent routing, fixed workflows, and ReAct fallback.
- `react`: the same cases sent directly through the ReAct path.

The cases cover out-of-scope requests, mixed business and non-business requests,
prompt injection, credential requests, device security bypass, abusive language,
and refund handling.

## Run

```powershell
python -m eval.run_golden --variants routed react --repeats 1 --max-calls 100 --max-tokens 100000
```

## Verification

- Full test suite: **61 tests passed**.
- Golden run: **20/20 cases completed** (10 cases x 2 variants).
- Sensitive cases for prompt injection, credentials, device security bypass, and administrator credentials were handled by `routed` with **zero model calls and zero tool calls**.
- The routed path preserved the allowed product response for mixed product plus non-e-commerce requests while refusing the unsupported portion.

The complete raw-response table is archived in
[`agent-golden-report.md`](agent-golden-report.md). The report intentionally leaves
`manual_result` blank: the dataset defines human expectations, while the recorded
responses are evidence for later review rather than automatic labels.
