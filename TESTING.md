# Testing Strategy

The normal suite is deterministic and does not require live Gemini, web, or API access. Live Gemini checks are separate manual checks.

## Current checkpoint

```powershell
pytest
```

The passing-test count is intentionally reported by each test run rather than maintained as a fixed documentation value.

## Covered areas

- TXT, JSON, and PDF extraction and `NormalizedSource`
- product identification and dynamic schema validation
- mocked Gemini extraction and malformed outputs
- evidence firewall quote/value/source/location validation
- missing attributes and fail-closed behavior
- cross-source consistency, conflicts, evidence preservation, and unit normalization
- deterministic confidence scoring and score boundaries
- end-to-end pipeline orchestration
- controlled manufacturer retrieval, allowlists, exact MPN verification, and web/PDF normalization
- governed source discovery, policy filtering, candidate deduplication, and discovery-to-verification boundaries
- mocked Brave provider configuration, response normalization, malformed/rate-limited responses, and missing-key behavior
- mocked Serper provider configuration, organic-result parsing, URL normalization, malformed/rate-limited responses, and missing-key behavior
- pilot policy lookup, exact configured domains, missing-policy behavior, and exact-MPN rejection through pilot policies
- catalogue CSV input and six-field preservation
- exact 252-column delivery schema and comparison
- controlled reference resolution and no silent fallback
- catalogue enrichment, evaluation comparison, review issues, and delivery gating
- batch ordering, duplicate-row handling, failure isolation, review/evaluation aggregation, and safe delivery rows
- controlled identity policy resolution, ephemeral runtime policies, authority-verifier gating, and unknown-row review

Tests use local controlled product, catalogue, delivery, and mock-reference fixtures. The expected delivery CSV is used for structure/evaluation only, never as product evidence.

`python scripts/run_real_pipeline.py` is a manual real-Gemini check and is not part of the normal suite.

Future work includes scale tests for bulk processing, official reference-data integration tests, and additional approved manufacturer domains.

The real Brave and Serper providers are not called by the normal suite. Runtime policy tests inject deterministic search,
authority, and retrieval fixtures. A real pilot requires the selected provider's API key and an explicitly selected row
list; it must not be run against all 1,000 rows as part of ordinary testing.
