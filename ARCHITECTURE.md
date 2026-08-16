# Architecture

The MVP has two connected workflows. The catalogue workflow reuses the existing document/evidence pipeline rather than introducing a second AI extraction system.

## Document/product-intelligence pipeline

```text
Source file (TXT/JSON/PDF)
        ↓
Extraction and NormalizedSource
        ↓
Product identification
        ↓
Dynamic attribute schema
        ↓
Evidence-backed attribute extraction
        ↓
Deterministic evidence firewall
        ↓
Cross-source validation and unit normalization
        ↓
Deterministic confidence scoring
        ↓
ProductIntelligenceResult
        ↓
Streamlit display / JSON export
```

Gemini is used for product identification/schema generation and source-backed value extraction. Python validates responses, preserves evidence, detects conflicts, normalizes supported units, and calculates confidence.

## Catalogue enrichment vertical slice

```text
CatalogInputRow from CSV
          ↓
Controlled manufacturer/brand/reference resolution
          ↓
  Controlled manufacturer/brand policy resolution
          ↓
  Governed candidate discovery / explicit approved URLs
        ↓
Exact MPN verification
        ↓
Web/PDF normalization to NormalizedSource
        ↓
Existing product-intelligence pipeline
        ↓
Controlled attribute/value/UOM mapping
        ↓
ReviewReport delivery gate
        ↓
252-column delivery row
        ↓
Optional evaluation-only comparison
```

The current catalogue implementation has generic single-row enrichment plus deterministic row-isolated batch orchestration. Batch processing preserves input order, records every row outcome, and requires externally supplied source configuration; it does not perform automatic discovery for all 1000 rows.

## Components

- `extraction/`: TXT, JSON, PDF parsing, `NormalizedSource`, and locations.
- `product_identification.py`: `ProductIdentificationResult` and `AttributeDefinition`.
- `attribute_extraction.py`: structured Gemini extraction, `AttributeEvidence`, and fail-closed checks.
- `cross_source_validation.py`: deterministic consistent/single-source/conflict/not-found validation.
- `unit_normalization.py`: safe physical-unit comparison.
- `confidence_scoring.py`: bounded explainable confidence.
- `pipeline.py`: document orchestrator and `ProductIntelligenceResult`.
- `manufacturer_enrichment.py`: approved-domain retrieval, exact MPN verification, and web/PDF conversion.
- `source_discovery.py`: deterministic queries, Brave and Serper search adapters, untrusted candidates, explicit policy filtering, selected-row pilot diagnostics, and discovery-to-verification diagnostics.
- `pilot_policies.py`: controlled manufacturer/brand governance policies with approved domains and reasons; raw
  `Part_Manuf` may be a distributor, so unresolved identities return no policy. The six original MPN entries are
  compatibility fixtures only; new products are resolved by controlled identity.
- `reference_data.py`: deterministic reference interfaces; fixtures are mock/test data.
- `catalog_input.py`, `delivery_schema.py`, `delivery_output.py`: catalogue and exact 252-column handling.
- `catalogue_enrichment.py`: generic row orchestration and evaluation comparison.
- `catalogue_batch.py`: ordered batch orchestration, failure isolation, safe delivery aggregation, and summary counts.
- `review.py`: typed review issues, statuses, and delivery gating.
- `ui.py` and `app.py`: document-oriented Streamlit MVP.

## Policy boundary

Policies are governance configuration, not product-specific application logic. They contain only controlled
manufacturer/brand identity, approved HTTPS domains, and governance metadata. They contain no attributes, delivery
values, or expected-output values. Raw manufacturer/brand text and search results never create trusted policies.

Runtime resolution is explicitly three-state: `KNOWN` uses the controlled policy registry; `RESOLVABLE` follows a
product-first flow:

```text
Untrusted candidate-domain discovery
        ↓
Domain-constrained exact-MPN search
        ↓
Retrieve actual HTTPS candidate page
        ↓
Verify exact MPN/product existence and site identity from page content
        ↓
Create ephemeral in-memory policy for the verified domain
        ↓
Existing enrichment/evidence firewall
```

Search titles, snippets, ranking, and retailer pages are never evidence and never create trusted policy. Candidate
domains must be HTTPS and non-retailer, and the retrieved source must pass the existing exact-MPN verification gate.
`UNKNOWN` becomes `NEEDS_REVIEW`. Runtime policies are never persisted automatically. An injected deterministic
authority or site-identity verifier remains available as a test/compatibility seam; it does not relax the retrieval or
exact-MPN requirements.

## Safety boundaries

Every accepted attribute retains evidence tied to a real source. The quote and value must occur in source text, and source/location metadata must match. Conflicts are preserved; no component chooses a winner. Delivery additionally requires controlled mapping/reference approval and excludes review or blocked attributes.

## Out of scope

RAG, graph databases, OCR/image processing, unrestricted source discovery, automatic full-catalogue searching, fuzzy identifier matching, automatic cross-reference, production persistence, authentication, cloud deployment, and a catalogue-management UI.
