# Data Schema

This document describes the implemented Pydantic models and catalogue structures. Accepted values are source-backed unless explicitly marked missing or under review.

## Source and product models

`NormalizedSource` contains a source identifier, source type, source name, extracted text, and source locations. Current types include `txt`, `json`, `pdf`, and controlled manufacturer `web` sources.

`ProductIdentificationResult` contains `product_type`, `product_category`, and `attributes`. Each `AttributeDefinition` contains `name`, `label`, `data_type`, optional `unit`, `required`, and `description`; it defines relevant attributes and does not contain values.

`AttributeEvidence` contains `source_id`, `source_name`, optional `location`, and `quote`.

`ExtractedAttribute` contains `name`, optional `value`, `status` (`found` or `not_found`), and optional `evidence`. Found values require evidence; not-found values contain neither value nor evidence.

`AttributeExtractionResult` contains the extracted attribute list for one normalized source.

`CrossSourceValidationResult` contains `ValidatedAttribute` entries with `name`, source-preserving `values`, `status`, and optional conflict information. Status is `consistent`, `conflict`, `single_source`, or `not_found`. Each source value retains its original value and evidence.

`ConfidenceScoringResult` contains `ConfidenceAssessment` entries with `name`, bounded `score`, `level` (`high`, `medium`, `low`), and human-readable `reasons`. Scores are calculated in Python.

`ProductIntelligenceResult` contains `sources`, `product_identification`, `dynamic_attribute_schema`, `extracted_attributes`, `validation`, and `confidence`.

## Catalogue models

`CatalogInputRow` represents exactly: `Mfg_Part_Num`, `Part_Desc`, `E1_Brand`, `Unilog_Brand`, `DIB_Brand`, and `Part_Manuf`. Raw values are preserved. `Part_Manuf` is not blindly copied into `MANUFACTURER_NAME`.

`DeliverySchema` validates an ordered header and enforces exactly 252 unique columns. Normal Streamlit execution loads the repository-owned canonical header from `data/unihack_delivery_schema.csv`; delivery rows are exact ordered mappings validated against that schema. External header loading remains available for fixtures and evaluation tools where explicitly required.

`CatalogueEnrichmentResult` contains `catalogue_row`, optional `pipeline_result`, `delivery_row`, `source_diagnostics`, `reference_resolution`, `mapping_diagnostics`, optional `evaluation_comparison`, and `review`.

`EvaluationComparison` and `EvaluationFieldDifference` compare generated output with a known-good row. Expected values are evaluation diagnostics only, never evidence.

## Review models

`ReviewIssue` contains `code`, `severity`, `scope`, `message`, optional `attribute_name`, `source_id`, `source_name`, `current_value`, and `affects_delivery`. Scope is `row`, `attribute`, `source`, or `evaluation`; severity is `info`, `warning`, `blocking`, or `error`.

`ReviewReport` contains `status` and `issues`. Status is `ready`, `needs_review`, `blocked`, or `failed`. It is diagnostic and does not repair or approve unsupported values.

## Controlled references

`ReferenceResolutionResult` contains `input_value`, `resolved_value`, `status`, `reference_type`, and `reason`. Resolution uses exact or case/whitespace-normalized matching only. Official UniHack reference masters are not present; test fixtures are explicitly mock data.

Bulk 1000-row result models and production persistence are future work.
