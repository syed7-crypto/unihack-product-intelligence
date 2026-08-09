# AI Pipeline

## Goal

The AI layer should not generate free-form text. It should return structured data that can be validated and exported.

## Where the LLM Fits

The LLM will be used for tasks that are hard to do reliably with simple rules alone:

- identifying the product category
- mapping text to the correct attribute schema
- extracting attributes from messy source content
- helping judge ambiguous missing or conflicting information

## What Should Stay Deterministic

These parts should be handled by Python rules where possible:

- file parsing
- text normalization
- simple unit or number checks
- duplicate value detection
- obvious conflict detection across sources
- schema formatting

This reduces hallucination risk and makes the system more predictable.

## Recommended AI Flow

1. Collect normalized source text and metadata.
2. Ask the model to identify the product type.
3. Ask the model to produce a dynamic attribute schema for that product type.
4. Ask the model to extract values into the schema.
5. Ask the model to return evidence references for each extracted value when possible.
6. Run validation checks in Python.
7. Mark conflicts, missing fields, and low-confidence fields.
8. Return the final JSON structure.

## Prompt Strategy

The prompts should be short, precise, and structured.

Important rules for prompts:

- provide a clear task
- give source text in a bounded format
- tell the model to return JSON only
- define the expected schema
- instruct the model not to guess when evidence is missing
- ask it to mark uncertain values instead of inventing them

## Structured Output Format

The LLM should return data in a predictable JSON structure such as:

- product name
- product category
- attributes list
- evidence references
- confidence value per field
- notes about uncertainty

The exact schema should be defined in DATA_SCHEMA.md.

## Validation Approach

After the model returns output, validate it in Python.

Examples:

- if two sources say different values for the same attribute, flag a conflict
- if a value does not match the expected type, flag it
- if the model invents an attribute not relevant to the product type, reject or review it
- if the model provides a value with no evidence, lower the confidence

## Confidence Approach

Confidence should not be a random number. It should be based on signals such as:

- whether the value appears in one source or several sources
- whether evidence is directly quoted or implied
- whether sources agree
- whether the value passed validation checks
- whether the model marked it as certain or uncertain

A simple scoring scale is enough for the MVP.

## Hallucination and Reliability Mitigation

To keep the MVP trustworthy:

- prefer structured prompts over open-ended prompts
- keep extraction tied to source text
- require evidence references where possible
- let Python handle obvious checks
- do not force a value when the source does not support it
- separate extracted facts from inferred/enriched facts

## Enrichment Guidance

If enrichment is used, it should be clearly labeled as inferred or suggested, not treated as source-confirmed truth.

## MVP Recommendation

For the first version, use one LLM call for category + schema + extraction only if it stays reliable. If that becomes too messy, split it into two simpler steps:

- step 1: identify product type and relevant schema
- step 2: extract attributes into the schema

That split is often easier for a beginner team to debug.
