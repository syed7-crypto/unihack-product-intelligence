# Testing Strategy

## Goal

The MVP should be tested on a small controlled dataset so we can trust the demo and catch obvious issues before submission.

This does not need a heavy testing framework. The goal is reliability for the hackathon MVP.

## What We Will Test

- Unit testing
- Input/extraction testing
- AI output/schema validation
- Missing attribute testing
- Conflict detection testing
- Confidence scoring testing
- End-to-end/demo testing

## Controlled Sample Dataset

Use 2 to 3 product categories only.

Recommended categories:

- Industrial valve
- SSD
- Pen or another simple product

For each category, prepare a small set of sample sources such as:

- one PDF
- one TXT file
- one JSON file

## Test Scenarios

### A. Normal Case

Two sources agree.

Example:

- PDF: 150 PSI
- TXT: 150 PSI

Expected result:

- value is extracted correctly
- confidence is high
- no conflict is reported

### B. Missing Data

An expected attribute is not present.

Example:

- the source contains pressure rating but not temperature range

Expected result:

- missing attribute is listed
- the system does not invent a value
- the output shows that the field needs review or remains empty

### C. Conflict

Sources disagree.

Example:

- PDF: 150 PSI
- TXT: 120 PSI

Expected result:

- conflict is detected
- both values are visible
- status is marked for verification

### D. Messy Input

The same attribute appears with different wording or formatting.

Example:

- 150 psi
- 150 PSI
- Pressure: 150 pounds per square inch

Expected result:

- the meaning is normalized
- the same attribute is matched correctly
- confidence remains reasonable if evidence is consistent

### E. Unsupported or Invalid Input

The system receives input it cannot parse well.

Example:

- broken file
- empty file
- unsupported format
- unreadable JSON

Expected result:

- the system fails gracefully
- the user gets a clear error message
- the pipeline does not crash silently

## Test Levels

### Unit Testing

Test small helper functions such as:

- source parsing helpers
- text normalization helpers
- schema generation helpers
- confidence scoring helpers
- conflict detection helpers

### Input/Extraction Testing

Check that PDFs, TXT files, and JSON files are turned into the normalized source format correctly.

### AI Output/Schema Validation

Check that the AI output matches the schema and that invalid fields are rejected or flagged.

### Missing Attribute Testing

Check that expected but absent attributes are listed correctly.

### Conflict Detection Testing

Check that mismatched values are not overwritten and are surfaced clearly.

### Confidence Scoring Testing

Check that confidence changes based on evidence, agreement, and validation results.

### End-to-End/Demo Testing

Run the full flow from input file to final structured JSON and UI display.

## Suggested MVP Checklist

1. Parse each supported input type.
2. Confirm normalized source output is correct.
3. Confirm product category identification works on sample data.
4. Confirm dynamic attribute schema generation returns the right fields.
5. Confirm missing and conflict cases are handled.
6. Confirm the final JSON is valid and exportable.
7. Confirm the Streamlit demo shows readable output.

## Test Principle

For this hackathon project, the best testing strategy is a small number of well-chosen cases that cover the important behavior, not a huge suite that delays progress.
