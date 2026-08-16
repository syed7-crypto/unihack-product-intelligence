# Judging Strategy

## Problem

Product catalogues often begin with sparse descriptions and inconsistent source material. A useful system must produce structured attributes without turning plausible guesses into accepted catalogue facts.

## MVP story

UniHack Product Intelligence combines limited catalogue/document inputs with explicitly verified manufacturer sources and returns structured, traceable product information.

The strongest demo points are:

- dynamic product identification and attribute schemas;
- evidence attached to every accepted value;
- fail-closed hallucination protection;
- preserved cross-source conflicts;
- deterministic unit normalization and explainable confidence;
- controlled manufacturer enrichment and exact MPN verification;
- constrained 252-column delivery mapping;
- human-review boundaries for unresolved, conflicting, or blocked data.

Python verifies source metadata, quotes, values, locations, units, references, conflicts, and delivery eligibility. Expected-output comparison remains separate from product evidence.

The architecture has been stress-tested across different categories, but the current catalogue implementation is a single-row vertical slice. It does not claim 1000-row batch processing.

The reusable boundaries support future batch orchestration, official reference ingestion, and broader source providers without weakening the evidence boundary.

RAG, graph databases, unrestricted discovery, fuzzy identifier matching, OCR, persistence, cloud deployment, and a full catalogue UI are outside the current MVP.
