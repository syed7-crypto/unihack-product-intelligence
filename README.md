# UniHack 2026 AI Product Intelligence

AI-powered product intelligence for industrial commerce.

## Problem

Industrial companies store product information in many places: PDFs, catalogs, web pages, spreadsheets, JSON feeds, and internal notes. The same product may appear with incomplete or conflicting details. Turning that scattered information into clean, structured, commerce-ready product data is slow and error-prone.

## Solution

This project will extract product information from documents, identify the product type, infer the relevant attributes for that type, and generate structured JSON with evidence, confidence, missing fields, and conflict detection.

The goal is not just document extraction. The goal is product intelligence: understanding what product it is, which attributes matter, what is missing, and where information disagrees across sources.

## Key Features

- PDF, TXT, and JSON input for the MVP
- Text extraction from uploaded files
- Product/category identification
- Dynamic attribute generation based on product type
- AI-assisted attribute extraction
- Missing attribute detection
- Basic validation
- Conflict detection across sources
- Confidence scores
- Evidence/source references
- Structured JSON output
- Simple dashboard for viewing results

## MVP Scope

The MVP will focus only on the features needed to demonstrate the core idea clearly and reliably.

### In Scope

- Upload or load PDF, TXT, and JSON inputs
- Extract plain text and structured source content
- Identify the product or product category
- Generate a product-specific attribute schema dynamically
- Extract attributes into structured JSON
- Detect missing attributes
- Detect conflicts between multiple sources
- Assign confidence scores
- Keep evidence references for extracted facts
- Show the final result in a simple UI
- Export the final JSON

### Out of Scope for MVP

- OCR/image extraction
- Large-scale batch processing
- Authentication
- Complex frontend apps
- Vector database / RAG
- Cloud deployment
- Advanced analytics
- Full catalog management system

## Recommended Tech Stack

### Backend

- Python
- PyMuPDF or pdfplumber for PDFs
- Standard JSON handling
- pandas later for CSV/Excel if needed
- Simple validation logic in Python

### AI Layer

- An LLM with structured JSON output
- Prompting designed for extraction, validation, and enrichment

### UI

- Streamlit is the recommended MVP UI

### Why Streamlit

Streamlit is a good fit because it is simple, fast to build, and beginner-friendly. It lets us focus on the product intelligence workflow instead of spending most of the time on frontend setup.

### Trade-off

A custom React frontend would be more flexible later, but it would also add more complexity than needed for an exam-constrained hackathon MVP.

## Architecture Summary

1. User uploads one or more source files.
2. The system extracts text or structured data from each file.
3. The product is identified and categorized.
4. A dynamic schema is generated for the product type.
5. The LLM extracts structured attributes from the source content.
6. Validation checks compare values across sources.
7. Missing fields, conflicts, confidence, and evidence are added.
8. The result is shown in the UI and exported as JSON.

## How to Run

This repository currently contains planning and documentation only.

When implementation begins, this section should include:

- environment setup
- dependency installation
- app launch command
- sample input files

## Current Status

- Documentation and MVP plan created
- No implementation code yet
- Ready for task breakdown and development

## Project Goal

Build a realistic hackathon MVP that demonstrates product intelligence, not just text extraction.
