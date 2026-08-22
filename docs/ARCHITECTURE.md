# Architecture

UniHack Product Intelligence has one evidence boundary shared by document processing and catalogue enrichment. The catalogue path adds governed source discovery and delivery mapping; it does not create a separate AI extraction architecture.

## End-to-end flow

```text
Catalogue input
      ↓
Controlled catalogue/manufacturer/brand/reference resolution
      ↓
Governed candidate discovery or explicitly approved URLs
      ↓
Exact MPN verification and source retrieval
      ↓
Source normalization (web, PDF, TXT, JSON)
      ↓
Product identification and dynamic attribute schema
      ↓
Evidence-backed attribute extraction
      ↓
Cross-source validation, unit normalization, confidence scoring
      ↓
Controlled attribute/UOM mapping
      ↓
Review report and delivery gate
      ↓
Ordered 252-column delivery row
```

## Trust boundaries

Gemini proposes product identity, relevant attributes, and values from supplied source text. Python controls admissibility. A found value must have matching source metadata, a quote present in that source, the value present in the quote, and valid location data when provided. Cross-source values and conflicts remain attached to their evidence.

Search titles, snippets, rankings, retailer pages, raw catalogue manufacturer text, and unverified candidate URLs are untrusted. They can guide discovery, but they cannot become product evidence or create a trusted policy. A retrieved source must pass policy, exact-MPN, and applicable identity checks before it enters normalization and extraction.

The controlled reference and governance fixtures in this repository are test data, not official UniHack masters. Runtime identity resolution can create only an ephemeral in-memory policy after the same verification boundary; it never changes the trusted registry.

## Fail-closed behavior

The system does not fill unsupported values. Missing or invalid evidence removes the affected attribute from accepted extraction and creates a review diagnostic; independently valid attributes may continue. Conflicts are preserved and no winner is selected. Missing, unresolved, blocked, or review attributes are excluded from delivery mapping.

Malformed model or pipeline responses can fail the row or run. Row-isolated batch processing preserves other row outcomes and records `ready`, `needs_review`, `blocked`, or `failed`. Review diagnostics describe the problem; they do not repair or approve data.

## Component responsibilities

- Catalogue and policy modules resolve controlled identities and approved domains.
- Discovery adapters return candidates and bounded telemetry only.
- Enrichment verifies exact MPNs and converts retrieved web/PDF content to normalized sources.
- Extraction and validation preserve evidence, compare values, normalize supported units, and score confidence.
- Mapping and delivery enforce reference approval, review gating, and the exact 252-column order.
- The Streamlit UI exposes Run, Results, Review, and Delivery views plus diagnostic downloads.
