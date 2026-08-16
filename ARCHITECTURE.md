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
- `source_discovery.py`: deterministic queries, the Brave search adapter, untrusted candidates, explicit policy filtering, selected-row pilot diagnostics, and discovery-to-verification diagnostics.
- `reference_data.py`: deterministic reference interfaces; fixtures are mock/test data.
- `catalog_input.py`, `delivery_schema.py`, `delivery_output.py`: catalogue and exact 252-column handling.
- `catalogue_enrichment.py`: generic row orchestration and evaluation comparison.
- `catalogue_batch.py`: ordered batch orchestration, failure isolation, safe delivery aggregation, and summary counts.
- `review.py`: typed review issues, statuses, and delivery gating.
- `ui.py` and `app.py`: document-oriented Streamlit MVP.

## Safety boundaries

Every accepted attribute retains evidence tied to a real source. The quote and value must occur in source text, and source/location metadata must match. Conflicts are preserved; no component chooses a winner. Delivery additionally requires controlled mapping/reference approval and excludes review or blocked attributes.

## Out of scope

RAG, graph databases, OCR/image processing, unrestricted source discovery, automatic full-catalogue searching, fuzzy identifier matching, automatic cross-reference, production persistence, authentication, cloud deployment, and a catalogue-management UI.
