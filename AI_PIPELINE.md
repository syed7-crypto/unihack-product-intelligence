# AI Pipeline

The AI boundary is intentionally narrow. Gemini proposes structured product information; deterministic Python decides whether the proposal is admissible.

## Gemini stages

1. Product identification and dynamic attribute-schema generation.
2. Attribute-value extraction for each `NormalizedSource`.

Product identification defines relevant attributes but does not extract their values. Attribute extraction is a separate stage.

## Evidence firewall

Prompts require only supplied source information, no outside knowledge or guessing, `not_found` when evidence is absent, and evidence for every `found` value.

Python validates every found value:

- evidence exists;
- `source_id` and `source_name` match the supplied source;
- an optional location exists in known source locations;
- the quote occurs in extracted source text using safe case/whitespace normalization;
- the proposed value occurs in the supporting quote using the same normalization.

Malformed or unsupported combinations raise existing extraction/pipeline errors. The system fails closed.

## Deterministic stages

Python performs parsing, Pydantic validation, evidence checks, cross-source comparison, supported unit normalization, confidence scoring, reference/UOM/mapping checks, review issue creation, and delivery gating.

Gemini is not asked to decide conflicts or generate confidence. Conflicting values and their evidence remain preserved; no winner is selected.

## Catalogue boundary

Catalogue enrichment accepts explicitly supplied approved manufacturer URLs/domains. The provider verifies the exact MPN before converting a web page or PDF to `NormalizedSource`. It performs no unrestricted discovery, fuzzy matching, or automatic cross-reference.

Delivery also requires controlled reference and attribute/UOM mapping approval. Repository reference fixtures are mock data because official UniHack masters are unavailable.

## Failure behavior

Missing or invalid evidence fails closed. Conflicts remain visible and score low. Missing, unresolved, blocked, or review attributes are not mapped into delivery fields. Review diagnostics do not repair or approve values.
