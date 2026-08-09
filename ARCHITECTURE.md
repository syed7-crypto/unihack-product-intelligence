# Architecture

## MVP Architecture

```text
User
↓
File Upload
↓
Input Parser
├── PDF
├── TXT
└── JSON
↓
Normalized Source
↓
Product Identification
↓
Dynamic Attribute Schema
↓
AI Extraction
↓
Python Validation
├── Missing Attributes
├── Conflict Detection
└── Confidence Calculation
↓
Structured Product JSON
↓
Streamlit UI
↓
JSON Export
```

## Component Overview

### User

The user uploads one or more product documents and reviews the structured result.

### File Upload

This is the entry point for the MVP. The supported inputs are PDF, TXT, and JSON.

### Input Parser

The parser reads each file type and turns it into usable content.

- PDF: extract text from document pages
- TXT: read plain text directly
- JSON: parse structured source data

The parser should keep simple source metadata such as filename and page number when available.

### Normalized Source

All inputs are converted into one internal format so the next steps do not care which file type was uploaded.

### Product Identification

This step identifies the product type or category. That decision determines which attributes matter.

### Dynamic Attribute Schema

The system does not use one fixed schema for every product. Instead, it generates a schema based on the identified product type.

### AI Extraction

The LLM extracts relevant attributes from the normalized source content and maps them into the dynamic schema.

### Python Validation

Python handles reliability checks after extraction.

- Missing Attributes: identify expected fields that were not found
- Conflict Detection: compare sources and flag mismatched values
- Confidence Calculation: assign an explainable score using evidence and validation signals

### Structured Product JSON

The final output is a structured JSON object that contains the extracted data, missing fields, conflicts, confidence, and evidence.

### Streamlit UI

The UI presents the result in a simple way that is easy to demo during the hackathon.

### JSON Export

The final structured JSON can be exported for submission or later use.

## Data Flow

1. The user uploads one or more files.
2. The input parser extracts the source content.
3. The content is normalized into a common internal format.
4. The system identifies the product type.
5. A dynamic attribute schema is generated for that product type.
6. The LLM extracts values into the schema.
7. Python validates the extracted data, checks for missing fields, and detects conflicts.
8. Confidence is calculated from evidence and validation signals.
9. The final structured product JSON is produced.
10. The Streamlit UI displays the result and allows JSON export.

## MVP Scope Guardrails

This architecture is intentionally small.

Not included in the MVP architecture:

- React frontend
- RAG or vector databases
- Cloud infrastructure
- Authentication
- OCR or image processing
- Large catalog workflows
- Batch orchestration systems

The architecture should stay easy to build and easy to explain before exams start.
