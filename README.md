# UniHack Product Intelligence

UniHack Product Intelligence enriches sparse catalogue rows and product documents into structured product data without turning plausible guesses into accepted facts. It is a hackathon MVP built around traceability, deterministic checks, and explicit human-review boundaries.

## Problem

Product catalogues commonly contain an MPN, a short description, and inconsistent manufacturer or brand fields. Useful source material may be distributed across manufacturer pages, PDFs, and documents. A system that fills gaps from unsupported inference can create confident-looking but unsafe catalogue data.

## Solution

The system combines Gemini for product identification, dynamic attribute schemas, and source-backed extraction with Python-controlled verification. Catalogue enrichment resolves controlled identities, discovers candidates under a domain policy, verifies the exact MPN, normalizes the source, and reuses the document pipeline. Accepted values retain source evidence; unresolved or conflicting data stays visible for review.

## Pipeline

```text
Catalogue row → controlled references → governed discovery
              → exact MPN verification → source normalization
              → identification → extraction → validation
              → controlled mapping → review gate → delivery
```

Document inputs follow the same core path after TXT, JSON, or selectable-text PDF normalization. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the trust boundaries.

## Key capabilities

- Evidence-backed attributes with source, quote, and location checks.
- Cross-source consistency and conflict preservation; no automatic conflict winner.
- Supported unit normalization and explainable confidence scoring.
- Controlled manufacturer, brand, reference, attribute, and UOM boundaries.
- Exact MPN verification for retrieved web/PDF sources.
- Ordered, row-isolated catalogue batch processing.
- Candidate, search, runtime, review, and evaluation diagnostics.

## Governance and verification

Search results, snippets, rankings, retailer pages, and raw manufacturer text are untrusted. A source becomes usable only after the configured policy and retrieval checks, exact MPN verification, and deterministic site-identity checks where applicable. Found values must cite text from the matching source. Invalid attribute proposals are excluded and reported; malformed pipeline responses fail the relevant run. Review diagnostics do not repair, approve, or choose values.

## Delivery output

The application uses the repository-owned [`data/unihack_delivery_schema.csv`](data/unihack_delivery_schema.csv) header as its canonical delivery schema. It enforces exactly 252 unique, ordered columns. Only reference-approved and review-clear attributes are mapped. Raw catalogue fields are preserved, while missing, unresolved, conflicting, blocked, or review attributes remain out of delivery. The repository does not include official UniHack manufacturer, brand, taxonomy, or other reference masters.

## Candidate telemetry

Candidate telemetry records the MPN, candidate URL/domain, ranking and score, fetch result, exact-MPN result, identity result, and rejection code. It is diagnostic rather than evidence. The Streamlit Delivery page also exports bounded runtime and search diagnostics without provider payloads.

## Review and status behavior

Each row receives one status: `ready`, `needs_review`, `blocked`, or `failed`. `ready` is delivery-eligible; `needs_review` identifies unresolved or warning-level issues; `blocked` records a blocking issue that prevents safe delivery; `failed` records an execution failure. The UI keeps results, review diagnostics, and safely gated delivery as separate views.

## Run the Streamlit demo

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Upload only a catalogue CSV, then run enrichment. The Run page displays the locked `UniHack Required Schema · 252 columns` and loads the canonical header internally. Live Gemini or search-backed runs require the relevant local environment configuration; do not commit credentials. `app.py` remains an alternate Streamlit launcher.

The concise judge walkthrough is in [docs/DEMO.md](docs/DEMO.md).

## Benchmark snapshot

The latest supplied 10-row benchmark snapshot contains:

| Status | Rows | Accepted attributes |
|---|---:|---:|
| `ready` | 6 | 46 |
| `needs_review` | 4 | 0 |
| `blocked` | 0 | 0 |
| **Total** | **10** | **46** |

`needs_review` is an important trust-preserving outcome, not a bad result. It
means the system has identified a case that requires human judgment instead
of silently accepting uncertain identity, source, conflict, or validation
data. The pipeline is designed to prefer a well-explained review decision to
an unsafe automatic delivery. See [docs/BENCHMARK.md](docs/BENCHMARK.md) for
the full benchmark context, latest timing, external-factor caveats, and
runtime optimization roadmap. The latest supplied 10-row run took
approximately **8 minutes 31 seconds**.
