# Real Gemini Baseline v2 connectivity rerun

## Scope and reproducibility

This rerun uses the baseline runner from `d8ecbd5`, the same 58 selected public Development and Validation cases, and dataset `benchmark-golden-evidence-review-v1`. The SQL Shed ODS content fingerprint matched the original run. The original [HTTP 400 report](../../reports/benchmark/real_gemini_baseline_v2.json) is preserved. The [preflight](../../reports/benchmark/real_gemini_connectivity_rerun_preflight.json) and [rerun data](../../reports/benchmark/real_gemini_baseline_v2_connectivity_rerun.json) are separate artifacts.

The runner stores the fixture versions, source hashes, active state, embedding version, chunk counts, and the cross-owner check at publication. The three temporary sources contain the previously authorized synthetic refund policy, reviewed synthetic PDF, and four approved line ranges of the table-design document. The runner withdraws the sources on exit. The holdout seal is used to check integrity. Holdout plaintext, the pending cases, and additional technical-document content were outside this run.

```bash
PYTHONPATH=src:tests .venv/bin/python -m benchmark.live_baseline \
  --allow-live-model --allow-four-reviewed-technical-spans --out /tmp/gemini-rerun.json
PYTHONPATH=src:tests .venv/bin/python -m benchmark.live_baseline \
  --verify-sql --out /tmp/gemini-rerun.json
```

The configured models were `gemini-3.5-flash-lite` and `gemini-embedding-001`. One preflight request per endpoint succeeded in 5,716 ms and 3,017 ms, respectively. Preflight is excluded from scored execution and reported API counts. The runner retained the existing limits of 110 generation calls, 30 embedding calls, 10.5 million reserved tokens, and a USD 20 estimated reservation ceiling.

## Results

All 58 selected cases ran. The 19 cases previously skipped during index preparation were exercised, with 16 passing and 3 failing. The temporary sources were withdrawn after the run. PostgreSQL confirmed their active version and index pointers were cleared.

| Metric | Numerator | Denominator | N/A cases |
| --- | ---: | ---: | ---: |
| Intent accuracy | 42 | 45 | 13 |
| Entity precision | 37 | 37 | 22 |
| Entity recall | 37 | 40 | 22 |
| Entity F1 | 74 | 77 | 22 |
| Plan validity | 4 | 8 | 50 |
| Tool route accuracy | 33 | 43 | 15 |
| SQL result accuracy | 17 | 23 | 35 |
| Executed SQL agreement with ODS | 17 | 17 | 41 |
| Retrieval Recall@6 | 12 | 12 | 46 |
| Retrieval MRR@6 (sum of reciprocal ranks) | 12 | 12 | 46 |
| Citation validity | 18 | 20 | 38 |
| Citation semantic support | 0 | 0 | 58 |
| Refusal and clarification guardrails | 16 | 16 | 42 |
| End-to-end task success | 46 | 58 | 0 |

Retrieval scores describe the small authorized fixture corpus and specifically labeled queries. Citation validity verifies source and quote evidence. Human judgment of semantic support remains N/A. A separate read-only reconciliation agreed with ODS for 18 structured requests over 13 cases.

Five SQL cases had semantically identical requests with omitted empty filters or groups. The report preserves their initial grades. The reconciled grades use the original captured result hashes and an exact match with independently verified results from the same ODS snapshot. Future runs normalize those two defaults before comparing requests. No labels, Agent routing, or SQL execution were modified. Reproduce the hash-based regrade with `--verify-sql --reconcile-captured-defaults --out <rerun report>`.

## Calls, latency, and failure attribution

- The scored run issued 46 generation requests, 43 of which returned successfully, and 22 successful Embedding requests. Three Embedding requests prepared the index and 19 were query requests. Preflight added one successful request to each endpoint. No fixed proposals or replay cases were executed. Successful generation responses were observed in 41 cases.
- Generation usage was 28,644 input and 3,770 output tokens (32,414 total). Embedding responses did not provide token counts. The reported standard paid estimate of USD 0.0180182 covers the 43 requests with usage. It excludes 22 Embedding requests and 3 failed generation attempts without usage, and excludes preflight.
- End-to-end latency P50/P95 was 10.164/18.251 seconds. The JSON contains task-type latency and per-case phase timings. Two cases retried once. One recovered after a read timeout and one failed after two HTTP 503 responses. No HTTP 429 was observed.
- The 12 failed cases fall into intent/entity (4), plan generation (4), SQL task result (2), citation validity (1), and external provider availability (1). The final 503 prevented an otherwise retrieved document from being used in the answer. That case retains its original citation-failure attribution in the JSON as well as the provider attribution.
- Both previously misrouted formal-definition questions still entered pgvector RAG. The over-refund count still entered Catalog, so its SQL statistic did not execute. The fourth prior failure, cohort horizon drift, safely requested clarification but produced no expected `item_return_rate` entity. These four cases remain failed.
- The three failures among the 19 recovered cases were one technical-document response blocked by 503 and two PDF questions on superseded OCR evidence and image coordinates that the Agent routed as metric requests.

The next experiment should isolate definition-versus-numeric intent routing using the reproduced misroutes before changing retrieval ranking.

## Regression

Python ran 218 tests with 217 passing and the separate opt-in Gemini test skipped. PostgreSQL, pgvector and Apple Vision integration tests were enabled. Frontend passed all 5 tests, TypeScript checking, and the production build.
