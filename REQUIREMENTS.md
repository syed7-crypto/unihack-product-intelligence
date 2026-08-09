# Requirements

## Functional Requirements

### Input Handling

- Accept PDF files in the MVP
- Accept TXT files in the MVP
- Accept JSON files in the MVP
- Support multiple sources for the same product when available

### Extraction

- Extract text or structured content from each input file
- Preserve source metadata such as filename and page number when possible

### Product Intelligence

- Identify the product or product category from the input
- Decide which attributes are relevant for that product type
- Extract the relevant attributes into a structured format
- Detect missing attributes for that product type
- Detect conflicting values across sources
- Produce a confidence score for extracted values
- Attach evidence or source references to extracted values when possible

### Output

- Generate structured JSON output
- Show the extracted result in a simple UI
- Allow JSON export

## Non-Functional Requirements

- The MVP should be easy to understand for a beginner team
- The system should be simple enough to build before exams
- The pipeline should be modular so later features can be added safely
- The AI output should be structured and machine-readable
- The system should be reliable enough to demonstrate on sample products
- The solution should work on local development machines first

## MVP Requirements

These are the minimum requirements for a strong hackathon demo.

- PDF, TXT, and JSON input
- Text extraction
- Product/category identification
- Dynamic attribute generation
- AI attribute extraction
- Missing attribute detection
- Basic validation
- Conflict detection
- Confidence scores
- Evidence/source references
- Structured JSON output
- Simple dashboard or viewer

## Future Requirements

These can be added only if time remains.

- Excel/CSV input
- URL input
- Image/OCR input
- Batch processing
- More advanced validation
- Product comparison
- Better frontend
- Cloud deployment
- Authentication

## Out-of-Scope Features for MVP

- Full production-grade catalog management
- Large-scale multi-tenant system
- OCR and image-heavy pipelines
- Vector database or RAG-based retrieval layer
- Advanced analytics dashboards
- Complex role-based access control
- Mobile app
- Heavy frontend engineering

## Requirement Notes

The most important product rule is that the system must understand the product type first and then generate the right attribute schema. A single fixed schema for all products would be too weak for this problem.
