# Judging Strategy

## What the Judges Need to See

The project should show that it solves the challenge in a meaningful way, not just that it uses an LLM.

## Innovation

The main innovation is dynamic product intelligence.

Instead of using one fixed schema for every product, the system first identifies the product type and then builds the relevant attribute schema. That makes the output more useful for real commerce data.

## Technical Implementation

The technical strength should come from the pipeline:

- input extraction
- product identification
- dynamic schema generation
- structured AI extraction
- validation
- conflict detection
- confidence scoring
- evidence tracking

This is stronger than a basic "upload file and ask the model" demo because it shows control, structure, and reliability.

## Business Relevance

Industrial commerce teams need clean product data to publish catalogs, support sales, and maintain consistency across channels.

The project addresses real business pain:

- less manual data cleanup
- better data consistency
- faster product onboarding
- better readiness for commerce systems

## Scalability

The MVP should suggest that the same pipeline can later scale to more documents and more product types.

Scalability is supported by:

- modular pipeline design
- source normalization
- dynamic schema generation
- structured JSON output
- clear extension points for batch processing and APIs

## Overall Impact

The project matters because it improves the quality and trustworthiness of product data. In commerce, bad data creates confusion, delays, and bad customer experiences.

## Why This Is Not Just a Basic LLM Extractor

A basic extractor would only summarize text or generate a flat JSON output.

This project goes further by:

- understanding the product category
- selecting only relevant attributes
- showing missing data
- detecting conflicts between sources
- attaching confidence and evidence
- producing output that is useful for commerce workflows

## Demo Message

A strong demo message could be:
"We turn messy product documents into structured, validated, evidence-backed product data that can be used in industrial commerce systems."

## What to Emphasize in Submission

- the problem is real and industry relevant
- the output is structured and trustworthy
- the system handles uncertainty instead of hiding it
- the design can extend to large catalogs later
- the MVP is focused and realistic
