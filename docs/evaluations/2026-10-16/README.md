# Published evaluation evidence — October 16, 2026

Main run: `20261016T114913Z-retrieval-4c7925`. This directory preserves a compact, versioned copy of the completed real knowledge-retrieval experiment. See [the benchmark report](../../RAG_EVALUATION_2026-10-16.md) and [findings](../../EVALUATION_FINDINGS_2026-10-16.md).

## Files and provenance

| File | Contents |
| --- | --- |
| `config.json` | Original run settings, dependency versions, baseline commit and source hashes |
| `queries.json` | All 190 original queries and designated target IDs |
| `index_audit.json` | Audit of 536 chunks, vector settings and embedding-provenance limitation |
| `rankings.jsonl` | All three ordered candidate lists, original retrieval scores, metrics and timings per query |
| `candidate_payloads.json` | Deduplicated original metadata/text for all 532 distinct chunks returned during the run |
| `rerank_calls.jsonl` | All 190 original response strings, usage, request parameters and canonical request-message hashes |
| `rerank_prompt.json` | System prompt and formatting template used for these rerank requests |
| `metrics.json`, `analysis.json` | Original aggregate output and independent subgroup/latency/bootstrap analysis |
| `manifest.json` | SHA-256 and byte sizes for published evidence and corresponding local source records |
| `verify.py` | Offline integrity, ranking, request/response, usage and optional bootstrap verification |
| `smoke/<run_id>/` | Original config, case statuses, metrics and reports from preliminary Agent/memory diagnostics |

Candidate payloads are deduplicated rather than truncated. Joining each ranking's `id` with `candidate_payloads.json` reconstructs its original result. Scores in `rankings.jsonl` remain the original retrieval scores; the LLM's reranking scores are in the response strings in `rerank_calls.jsonl`. Ranking order records the effect of reranking.

Request messages can be reconstructed from the prompt, original hybrid candidate order and the first 180 characters of each candidate text. The canonical hash uses UTF-8 JSON with sorted keys, `ensure_ascii=False` and compact separators. The verifier checks those hashes against the recorded requests.

JSON metadata and reports were normalized to LF line endings; values were not changed. `.gitattributes` preserves LF for this bundle. Manifest source hashes refer to the untouched local files; bundle hashes refer to the published representation. The manifest is an integrity record, not third-party attestation. The run preceded the publishing commit, so its configuration correctly records the earlier Git baseline and individual code hashes.

Full raw requests/events remain locally in ignored `eval/results/`; local vector storage, credentials, browser cookies and environments are not included. The raw source-file hashes identify those originals without publishing repeated logs.

## Verify without model calls

From the repository root, using Python 3.11+:

```bash
python docs/evaluations/2026-10-16/verify.py

# Also reproduce the 10,000-resample document-cluster intervals; requires NumPy
python docs/evaluations/2026-10-16/verify.py --bootstrap
```

Verification reads the committed evidence only and makes no network calls. It checks all bundle hashes, complete query coverage, target-derived metrics, candidate preservation, complete finite rerank scores, score-derived sorting, request fingerprints, usage totals, subgroup results and latency statistics. The optional bootstrap reproduces the saved paired intervals. It does not rerun retrieval or establish the historical embedding model's identity.

Source code/data hashes in `config.json` are byte hashes from the original Windows run; line-ending conversion on another checkout can change them. They document provenance rather than requiring all future revisions to match the old source. Frozen ranking evidence and manifest hashes remain independently verifiable.

After this run, publication checks found and fixed an LF/CRLF portability issue in the Agent draft manifest validation. That fix changes the current evaluator/manifest, not this frozen run's configuration, labels, rankings or scores. The final offline regression suite has 60 tests; the pre-publication suite had 59.

## Preliminary smoke evidence

- `20261016T094049Z-agent-8e78fd`: three normal scenarios in both variants, plus a fault case interrupted by budget and one unstarted counterpart. The original scorer labeled the interruption `failed_execution`; interpret it as incomplete evaluation, not an Agent quality failure. Normal-case semantic judgments are pending.
- `20261016T094631Z-agent-40d097`: separate invalid-JSON and temporary-execution-error tests in both variants. All four executions completed; semantic recovery review is pending.
- `20261016T094336Z-memory-446e8b`: one fixed 24-turn script with 24 field checks per strategy. This is controlled extraction, not shopping-task success.

These snapshots preserve original reports, including their limitations. Full smoke event/turn logs remain local and are needed for semantic review; the public snapshots alone do not establish task correctness. They predate the final evaluation-assembly adjustment that omits an unavailable RAG tool. The current 100-scenario draft set has not received a full independently reviewed Agent run.
