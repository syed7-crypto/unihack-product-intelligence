# Benchmark

This page records the latest verified artifacts available in the repository state reviewed on 2026-08-22. It reports what was observed, not a forecast for live web or Gemini runs.

## Catalogue snapshot

The checked-in [result.csv](../result.csv) contains 10 rows:

| Status         |   Rows | Accepted attributes |
| -------------- | -----: | ------------------: |
| `ready`        |      2 |                   9 |
| `needs_review` |      7 |                   0 |
| `blocked`      |      1 |                   0 |
| `failed`       |      0 |                   0 |
| **Total**      | **10** |               **9** |

The checked-in [review.csv](../review.csv) contains 15 review issues. [candidate_telemetry.csv](../candidate_telemetry.csv) contains 14 candidate records, including candidates that were not considered, not fetched, not verified, conflicted, or verified. The snapshot is based on 10 selected output rows, not the full 1,000-row input catalogue.

## Runtime

The deterministic automated suite was run from the repository virtual environment and reported:

```text
361 passed, 1 warning, 28 subtests passed in 2.42s
```

That is test-suite runtime, not enrichment runtime. The checked-in catalogue result CSV does not contain a batch-duration value, so no catalogue runtime number is claimed here. The Streamlit run measures elapsed duration and can export aggregate `runtime_diagnostics.csv`; its value depends on provider responses, network conditions, and configuration.

## Status meanings

- `ready`: the row passed the review and delivery gates used by the current pipeline.
- `needs_review`: the row has unresolved or warning-level issues and is not treated as fully approved delivery.
- `blocked`: a blocking issue prevents safe delivery, such as a pipeline failure after source verification.
- `failed`: execution failed without producing a safe row outcome.

Accepted attributes are counted after controlled mapping, not merely after model extraction. Review or blocked attributes are not mapped into delivery.

## Interpretation

The result is intentionally mixed: rows without trustworthy source or controlled identity evidence remain reviewable instead of being completed by inference. External search and API responses vary between runs, so candidate counts, source availability, statuses, accepted attributes, and elapsed runtime can change. The snapshot should therefore be used as a reproducible record of one checked-in run, not as a guaranteed live benchmark or a claim of full-catalogue coverage.
