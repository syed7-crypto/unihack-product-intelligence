# Development Plan

## Goal

Deliver a small but impressive hackathon MVP before the exam period becomes a blocker.

## Milestone 1: Scope Freeze

Target outcome:

- final MVP scope is written down
- no extra features are added without a clear reason
- sample product types are chosen

## Milestone 2: Data and Extraction

Target outcome:

- a few sample PDFs, TXT files, and JSON files are ready
- text extraction works
- all sources are normalized into one internal format

## Milestone 3: Product Intelligence Core

Target outcome:

- product/category identification works
- dynamic schema generation works
- structured AI extraction works
- missing fields and conflicts are visible

## Milestone 4: Output and Demo UI

Target outcome:

- structured JSON is produced
- results are visible in a simple Streamlit app
- export works
- the demo can be explained clearly

## Milestone 5: Polish

Target outcome:

- error handling is acceptable
- sample outputs look good
- documentation matches the actual implementation
- the project is ready for submission/demo

## Suggested Implementation Order

1. Freeze the MVP scope.
2. Pick 2 or 3 sample product types.
3. Prepare sample documents for those products.
4. Build input extraction and normalization.
5. Define the output schema.
6. Build product/category identification.
7. Build dynamic attribute generation.
8. Build AI extraction.
9. Add validation and conflict checks.
10. Build the Streamlit UI.
11. Add JSON export.
12. Polish the demo and documentation.

## Must Be Finished Before Exams

These are the most important items to complete early.

- scope freeze
- sample data
- extraction pipeline
- product/category identification
- dynamic schema generation
- structured extraction
- missing attribute detection
- conflict detection
- confidence scoring
- simple UI

## Can Be Postponed

- Excel/CSV support
- URL support
- OCR/image support
- batch processing
- cloud deployment
- advanced analytics
- authentication
- large catalog features

## Practical Advice

The safest plan is to make the first demo work for a small number of sample products instead of trying to support everything. A narrow, reliable demo is much better than a large unfinished system.
