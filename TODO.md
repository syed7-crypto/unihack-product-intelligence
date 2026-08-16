# Living Backlog

Completed MVP work is recorded so the backlog does not present implemented features as unfinished.

## Completed MVP

- [DONE] TXT/JSON/PDF extraction and `NormalizedSource`
- [DONE] Product identification and dynamic attribute schema
- [DONE] Evidence-backed extraction and hallucination firewall
- [DONE] Cross-source validation and deterministic unit normalization
- [DONE] Explainable confidence scoring
- [DONE] Streamlit document UI and JSON export
- [DONE] Catalogue input adapter and six-field preservation
- [DONE] Exact 252-column delivery schema and comparison
- [DONE] Controlled reference-resolution interfaces
- [DONE] Controlled manufacturer retrieval and exact MPN verification
- [DONE] Governed manufacturer-source candidate discovery and verification boundary
- [DONE] Concrete Brave web-search provider with fail-closed configuration
- [DONE] Generic single-row catalogue enrichment
- [DONE] Row-isolated catalogue batch orchestration with deterministic summaries
- [DONE] Unified review/exception layer and delivery gating

## Current/next priority

- [TODO] Configure authoritative sources for the remaining catalogue rows.
- [TODO] Add resumability and performance tests before large-scale execution.

## Reference data

- [TODO] Obtain and ingest official UniHack manufacturer and brand masters.
- [TODO] Obtain and ingest official taxonomy, attribute/LOV, and UOM masters.
- [TODO] Replace mock fixtures only after official data contracts are available.

## Source discovery and enrichment

- [DONE] Define governed discovery with explicit allowlists.
- [TODO] Add an authoritative manufacturer cross-reference provider.
- [TODO] Expand approved manufacturer domains through tested configuration.
- [TODO] Add a small, explicitly selected real-search pilot configuration for manually verified manufacturers.
- [TODO] Preserve exact MPN verification and the evidence firewall.

## Evaluation

- [TODO] Build repeatable reports over known-good rows.
- [TODO] Separate coverage, review rate, mapping accuracy, and field-level differences.
- [TODO] Add regression fixtures for more product categories.

## UI/demo

- [TODO] Add a catalogue-row review experience after the backend batch contract is stable.
- [TODO] Expose review issues and evaluation diagnostics in a future UI.
- [TODO] Keep the current document UI unchanged until catalogue UI requirements are defined.

## Production/future

- [TODO] Production persistence and audit storage.
- [TODO] Authentication, authorization, and multi-tenant controls.
- [TODO] Cloud deployment and monitoring.
- [TODO] OCR/image processing.
- [TODO] RAG or graph-backed retrieval only if a later requirement justifies it.
- [TODO] Advanced analytics and product comparison.

## Explicitly out of scope now

Unrestricted web search, fuzzy manufacturer/MPN matching, automatic identifier guessing, unsupported value generation, and treating expected-output rows as evidence.
