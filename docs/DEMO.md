# Demo Walkthrough

## 1. Input

Open the Streamlit app and go to **Run**. Upload a catalogue CSV. Upload the expected-output CSV when you want the exact 252-column delivery schema; the app requires that schema before it can create delivery rows.

## 2. Run

Start enrichment. The app shows the governed path from discovery through delivery. Search candidates remain untrusted until retrieval, exact MPN verification, and the applicable identity checks succeed. No enrichment result is fabricated in the UI.

## 3. Results

Open **Results** to filter by MPN, manufacturer, or status. Inspect a row to see verified sources, accepted attributes, evidence, validation, confidence, and mapping outcomes.

## 4. Review

Open **Review** for unresolved identity, source, extraction, conflict, or mapping diagnostics. Review issues are explanations, not automatic repairs. Rows needing review or blocked rows do not silently become delivery-ready.

## 5. Delivery

Open **Delivery** to inspect and download the safely gated output. The page also offers candidate telemetry, aggregate runtime diagnostics, and search diagnostics. Delivery is limited to fields that passed the current reference, evidence, and review gates.

## What to look at

1. Evidence is attached to accepted values and comes from retrieved source text.
2. Search results and retailer pages are not accepted as evidence by themselves.
3. Exact MPN verification and identity checks precede extraction.
4. Conflicts and missing evidence stay visible instead of being guessed through.
5. The status summary distinguishes `ready`, `needs_review`, `blocked`, and `failed`.
6. The final output is constrained to the supplied 252-column schema.
