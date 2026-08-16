# Requirements and Scope

## Implemented document intelligence

- TXT, JSON, and PDF sources, including multiple sources for one product.
- Normalized source metadata, text, and locations.
- Product/category identification and dynamic attribute schema generation.
- Structured attribute extraction with source evidence.
- Deterministic evidence validation and fail-closed missing-value behavior.
- Missing attributes, cross-source conflicts, safe unit comparison, and explainable confidence.
- Structured JSON output and a document-oriented Streamlit UI.

## Implemented catalogue vertical slice

- Six-field CSV input through `CatalogInputRow`.
- Controlled manufacturer and brand/reference resolution.
- Explicit approved manufacturer URLs/domains and exact MPN verification.
- Web/PDF normalization into the existing pipeline.
- Evidence-backed controlled attribute/UOM mapping.
- Exact 252-column delivery output.
- Evaluation-only comparison with known-good rows.
- Typed review issues and delivery gating.
- Row-isolated batch orchestration with ordered outcomes and deterministic summaries.

## Safety requirements

Unsupported AI-generated values must not enter accepted extraction or delivery. Conflicts must not be silently resolved. Manufacturer, brand, taxonomy, attribute, value, and UOM fields must not be guessed when controlled approval is required. Raw catalogue fields remain preserved even when enrichment is blocked.

## Not implemented

- Automatic enrichment of all 1000 rows without supplied source configuration.
- Bulk-scale performance, resumability, and operational orchestration.
- Unrestricted automatic source discovery.
- Authoritative manufacturer cross-reference service.
- Official UniHack manufacturer, brand, taxonomy, attribute/LOV, or UOM master ingestion.
- Production persistence, cloud deployment, authentication, and multi-tenant management.
- OCR/image processing, RAG, graph databases, advanced analytics, or a catalogue-management UI.

The MVP is local-first, modular, and designed for a focused hackathon demonstration.
