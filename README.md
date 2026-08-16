# UniHack 2026 — Product Intelligence MVP

UniHack Product Intelligence turns limited product documents or a catalogue row plus approved manufacturer sources into structured, evidence-backed product data. The MVP is deliberately fail-closed: unsupported facts are not accepted into delivery output.

## Implemented MVP

Document intelligence includes TXT, JSON, and selectable-text PDF extraction into `NormalizedSource`, Gemini product identification and dynamic schemas, evidence-backed value extraction, the deterministic evidence firewall, cross-source validation, safe unit normalization, and explainable confidence scoring.

The catalogue workflow includes typed CSV input, controlled manufacturer/brand/reference resolution, a small manually verified manufacturer/brand governance-policy registry, governed candidate discovery under an explicit domain policy, provider-neutral Brave and Serper Web Search API adapters, exact MPN verification, web/PDF normalization, generic single-row enrichment, safe row-isolated batch orchestration, the exact 252-column delivery schema, controlled attribute mapping, evaluation-only comparison, and the unified review/exception layer.

Reference fixtures are mock/test data. No official UniHack reference masters are present in the repository.

## Architecture

```text
Document files → normalize → identify product/schema → extract values
               → evidence firewall → validate → confidence

Catalogue row → controlled references → manufacturer/brand policy → approved URLs → exact MPN
              → normalize sources → existing document pipeline
              → controlled mapping → review gate → 252-column row
```

The catalogue workflow reuses the document/evidence pipeline; it does not create a second AI extraction architecture.

## Safety and review

Gemini must use only supplied source text. Found values require matching source metadata, a quote present in source text, the value present in that quote, and valid location data. Conflicts remain visible. Validation, unit comparison, confidence, reference approval, and delivery gating are deterministic Python logic.

`ReviewReport` uses `ready`, `needs_review`, `blocked`, and `failed`. Review diagnostics never repair values, select conflict winners, or approve unsupported data. Blocked/review attributes are not mapped, while raw catalogue fields remain preserved.

## Limitations

- No official UniHack manufacturer, brand, taxonomy, attribute/LOV, or UOM masters are included.
- Manufacturer enrichment uses explicitly supplied approved URLs/domains or a transient runtime policy whose
  candidate domains have passed the same governance and verification boundary.
- No unrestricted discovery, automatic batch searching, fuzzy manufacturer/MPN matching, or automatic cross-reference provider. Discovery candidates remain untrusted until the provider verifies them.
- Real discovery requires `BRAVE_SEARCH_API_KEY`; missing configuration fails explicitly and never falls back to fake results.
- Serper is an alternative configured provider using `SERPER_API_KEY`; the caller chooses the provider.
- The governance-policy registry is not official UniHack reference data and does not cover all catalogue manufacturers.
- Catalogue `Part_Manuf` values may identify distributors or catalogue organizations rather than manufacturers. Raw
  manufacturer or arbitrary brand text never creates a trusted policy; unresolved identities remain blocked/reviewed.
- The six original pilot MPN fixtures remain as compatibility entries. New products resolve policies by controlled
  manufacturer/brand identity, not by product-specific application branches.
- Batch discovery is optional and policy-gated; there is no automatic discovery for the full 1000-row catalogue.
- Optional runtime resolution has three states: `KNOWN` uses a controlled policy; `RESOLVABLE` uses product-first
  resolution: untrusted candidate-domain discovery, domain-constrained exact-MPN search, retrieval of the actual
  manufacturer page, and deterministic site-identity verification before creating an ephemeral in-memory policy;
  `UNKNOWN` becomes `NEEDS_REVIEW`. Search titles, snippets, ranking, and retailer results never become evidence or
  trusted policy. Runtime policies never modify the trusted registry.
- No RAG, graph database, OCR/image processing, production persistence, cloud deployment, or catalogue-management UI.

## Setup and usage

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

For live Gemini calls, create `.env` with `GEMINI_API_KEY` at the repository root. The key is never printed by the application.

For a real, caller-selected discovery pilot, configure `BRAVE_SEARCH_API_KEY` and pass explicit manufacturer policies/domains. The discovery adapter returns candidates only; the existing enrichment provider must still verify every source.

Manual real-API check:

```powershell
python scripts/run_real_pipeline.py
```

Run the automated suite:

```powershell
pytest
```

The current automated suite reports its exact passing count when run; the count is not a permanent contract.

## Repository structure

- `src/product_intelligence/` — pipeline, catalogue, review, and UI modules
- `tests/` — deterministic unit and integration tests
- `samples/industrial_valve/` — controlled TXT/JSON/PDF sources and output
- `scripts/run_real_pipeline.py` — manual real-API runner
- `app.py` — Streamlit entry point

This is a focused hackathon MVP. Scale and production platform features remain future work.
