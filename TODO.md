# TODO

## Legend

- Status: TODO / IN PROGRESS / DONE
- Priority: MUST / SHOULD / COULD

## Phase 1: MVP Foundation

| Status | Priority | Task                                                                  |
| ------ | -------- | --------------------------------------------------------------------- |
| TODO   | MUST     | Define the final MVP scope and freeze it before implementation starts |
| TODO   | MUST     | Choose the MVP stack: Python + Streamlit + one LLM provider           |
| TODO   | MUST     | Create sample input files for 2 to 3 product types                    |
| TODO   | MUST     | Build a text extraction layer for PDF, TXT, and JSON                  |
| TODO   | MUST     | Normalize all extracted content into one internal format              |
| TODO   | MUST     | Define the product JSON schema and evidence format                    |
| TODO   | MUST     | Implement product/category identification                             |
| TODO   | MUST     | Implement dynamic attribute generation per product type               |
| TODO   | MUST     | Implement AI-based structured attribute extraction                    |
| TODO   | MUST     | Add missing attribute detection                                       |
| TODO   | MUST     | Add simple deterministic validation checks                            |
| TODO   | MUST     | Add conflict detection across multiple sources                        |
| TODO   | MUST     | Add confidence scoring rules                                          |
| TODO   | MUST     | Build a simple Streamlit interface                                    |
| TODO   | MUST     | Add JSON export                                                       |

## Phase 2: Improved Usability

| Status | Priority | Task                                           |
| ------ | -------- | ---------------------------------------------- |
| TODO   | SHOULD   | Add Excel/CSV input                            |
| TODO   | SHOULD   | Improve the UI layout and result presentation  |
| TODO   | SHOULD   | Add better error messages and input validation |
| TODO   | SHOULD   | Add URL extraction if time permits             |
| TODO   | SHOULD   | Add a product comparison view                  |
| TODO   | SHOULD   | Add batch processing for several files at once |

## Phase 3: Optional Stretch Goals

| Status | Priority | Task                                                     |
| ------ | -------- | -------------------------------------------------------- |
| TODO   | COULD    | Add OCR/image extraction                                 |
| TODO   | COULD    | Add large-catalog processing support                     |
| TODO   | COULD    | Add cloud deployment                                     |
| TODO   | COULD    | Add advanced analytics                                   |
| TODO   | COULD    | Add vector database or RAG only if there is a clear need |
| TODO   | COULD    | Add authentication only if the app becomes multi-user    |

## Suggested Work Order

1. Freeze scope.
2. Prepare sample data.
3. Build extraction.
4. Define the schema.
5. Implement product identification and dynamic schema generation.
6. Add LLM extraction.
7. Add validation and conflict checks.
8. Build the Streamlit UI.
9. Add export and polish.

## Notes

The most important rule is to keep the first version small. If a feature does not clearly help product intelligence, accuracy, or the hackathon demo, it should wait.
