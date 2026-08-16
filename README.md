# UniHack 2026 — Product Intelligence MVP

UniHack Product Intelligence turns limited product documents or a catalogue row plus approved manufacturer sources into structured, evidence-backed product data. The MVP is deliberately fail-closed: unsupported facts are not accepted into delivery output.

## Implemented MVP

Document intelligence includes TXT, JSON, and selectable-text PDF extraction into `NormalizedSource`, Gemini product identification and dynamic schemas, evidence-backed value extraction, the deterministic evidence firewall, cross-source validation, safe unit normalization, and explainable confidence scoring.

The catalogue vertical slice includes typed CSV input, controlled manufacturer/brand/reference resolution, explicitly approved manufacturer URLs, exact MPN verification, web/PDF normalization, generic enrichment orchestration, the exact 252-column delivery schema, controlled attribute mapping, evaluation-only comparison, and the unified review/exception layer.

Reference fixtures are mock/test data. No official UniHack reference masters are present in the repository.

## Architecture

```text
Document files → normalize → identify product/schema → extract values
               → evidence firewall → validate → confidence

Catalogue row → controlled references → approved URLs → exact MPN
              → normalize sources → existing document pipeline
              → controlled mapping → review gate → 252-column row
```

The catalogue workflow reuses the document/evidence pipeline; it does not create a second AI extraction architecture.

## Safety and review

Gemini must use only supplied source text. Found values require matching source metadata, a quote present in source text, the value present in that quote, and valid location data. Conflicts remain visible. Validation, unit comparison, confidence, reference approval, and delivery gating are deterministic Python logic.

`ReviewReport` uses `ready`, `needs_review`, `blocked`, and `failed`. Review diagnostics never repair values, select conflict winners, or approve unsupported data. Blocked/review attributes are not mapped, while raw catalogue fields remain preserved.

## Limitations

- No official UniHack manufacturer, brand, taxonomy, attribute/LOV, or UOM masters are included.
- Manufacturer enrichment uses explicitly supplied approved URLs/domains only.
- No unrestricted discovery, fuzzy manufacturer/MPN matching, or automatic cross-reference provider.
- No bulk 1000-row execution yet; the catalogue workflow is a single-row vertical slice.
- No RAG, graph database, OCR/image processing, production persistence, cloud deployment, or catalogue-management UI.

## Setup and usage

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

For live Gemini calls, create `.env` with `GEMINI_API_KEY` at the repository root. The key is never printed by the application.

Manual real-API check:

```powershell
python scripts/run_real_pipeline.py
```

Run the automated suite:

```powershell
pytest
```

There are 100 passing tests at this checkpoint; the count is not a permanent contract.

## Repository structure

- `src/product_intelligence/` — pipeline, catalogue, review, and UI modules
- `tests/` — deterministic unit and integration tests
- `samples/industrial_valve/` — controlled TXT/JSON/PDF sources and output
- `scripts/run_real_pipeline.py` — manual real-API runner
- `app.py` — Streamlit entry point

This is a focused hackathon MVP. Scale and production platform features remain future work.
