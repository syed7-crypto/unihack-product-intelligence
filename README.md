# UniHack 2026 — Product Intelligence (MVP)

Compact hackathon MVP that extracts product data from TXT/JSON/PDF inputs, identifies product type, generates a dynamic attribute schema, extracts attribute values with evidence, validates across sources, and scores confidence. Includes a lightweight Streamlit UI to inspect results and export JSON.

## Current implemented features

- TXT, JSON and PDF input extraction (normalized source objects)
- Product identification that returns a dynamic attribute schema
- Attribute extraction per source with evidence and status
- Cross-source validation (consistent / single_source / conflict / not_found)
- Deterministic confidence scoring per validated attribute
- Simple Streamlit UI entrypoint (`app.py`) and a pipeline orchestrator
- End-to-end pipeline script for manual real-API runs (`scripts/run_real_pipeline.py`)

## Architecture / Pipeline

1. Extract text/structured content from each input (txt/json/pdf).
2. Identify product type and produce a dynamic attribute schema.
3. Extract attribute values from each source, attach evidence & locations.
4. Validate attributes across sources (preserve conflicting values).
5. Score confidence and present results in the UI / export as JSON.

## Quick setup

1. Create and activate a virtual environment:

```bash
python -m venv .venv
# Windows (PowerShell): .\.venv\Scripts\Activate.ps1
# macOS / Linux: source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Add your Gemini API key to a `.env` file at the repository root (required for any real Gemini calls):

```
GEMINI_API_KEY=your_real_gemini_api_key_here

```

## Run the app

- Start the Streamlit UI (from project root):

```bash
streamlit run app.py
```

- Or run the manual real-API pipeline against the controlled samples:

```bash
python scripts/run_real_pipeline.py
```

The Gemini client expects `.env` at the project root and will raise if `GEMINI_API_KEY` is missing.

## Run tests

- From the repository root run:

```bash
pytest
```

Current test coverage: 37 unit tests across the `tests/` suite.

## Samples

- Controlled samples are in `samples/industrial_valve/` (txt, json, pdf variants). Use these to exercise the pipeline and the manual script.

## Notes / Hackathon constraints

- This README documents the implemented MVP only — the project is intentionally compact and focused for a hackathon demo. Production concerns (authentication, OCR, large-scale batch processing, DB persistence, cloud deployment) are out of scope for this MVP.
