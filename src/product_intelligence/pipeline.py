"""End-to-end orchestration for the product intelligence pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, Field

from .attribute_extraction import (
    AttributeExtractionError,
    AttributeExtractionResult,
    StructuredGeminiClient,
    extract_attribute_values,
)
from .confidence_scoring import ConfidenceScoringResult, calculate_confidence
from .cross_source_validation import (
    CrossSourceValidationError,
    CrossSourceValidationResult,
    validate_cross_source,
)
from .extraction import ExtractionError, NormalizedSource, extract_file
from .gemini_client import create_gemini_client
from .product_identification import (
    AttributeDefinition,
    ProductIdentificationError,
    ProductIdentificationResult,
    identify_product,
)


class SourceSummary(BaseModel):
    """Serializable metadata for a normalized input source."""

    source_id: str
    source_type: str
    source_name: str
    locations: list[str] = Field(default_factory=list)


class ProductIntelligenceResult(BaseModel):
    """Final result returned by the complete product intelligence pipeline."""

    sources: list[SourceSummary] = Field(min_length=1)
    product_identification: ProductIdentificationResult
    dynamic_attribute_schema: list[AttributeDefinition] = Field(min_length=1)
    extracted_attributes: list[AttributeExtractionResult] = Field(min_length=1)
    validation: CrossSourceValidationResult
    confidence: ConfidenceScoringResult


class ProductIntelligencePipelineError(RuntimeError):
    """Raised when a pipeline stage fails with an actionable stage name."""


def run_pipeline(
    source_files: Sequence[str | Path | NormalizedSource],
    client: StructuredGeminiClient | None = None,
) -> ProductIntelligenceResult:
    """Run extraction, AI stages, validation, and scoring for source files.

    The product is identified once from the first source. Its resulting dynamic
    schema is then applied consistently to every source in ``source_files``.
    Pass a mocked structured client in tests to avoid live Gemini calls.
    """
    if not source_files:
        raise ProductIntelligencePipelineError("At least one source file is required.")

    sources = _extract_sources(source_files)
    gemini_client = client
    try:
        gemini_client = gemini_client or create_gemini_client()
        product_identification = identify_product(sources[0], gemini_client)
    except ProductIdentificationError as error:
        raise ProductIntelligencePipelineError(
            "Product identification failed."
        ) from error
    except Exception as error:
        raise ProductIntelligencePipelineError(
            "Could not initialize the product intelligence pipeline."
        ) from error

    try:
        extracted_attributes = [
            extract_attribute_values(source, product_identification, gemini_client)
            for source in sources
        ]
    except AttributeExtractionError as error:
        raise ProductIntelligencePipelineError(
            "Attribute value extraction failed."
        ) from error

    try:
        validation = validate_cross_source(extracted_attributes, product_identification)
    except CrossSourceValidationError as error:
        raise ProductIntelligencePipelineError(
            "Cross-source validation failed."
        ) from error

    confidence = calculate_confidence(validation)
    return ProductIntelligenceResult(
        sources=[_source_summary(source) for source in sources],
        product_identification=product_identification,
        dynamic_attribute_schema=product_identification.attributes,
        extracted_attributes=extracted_attributes,
        validation=validation,
        confidence=confidence,
    )


def _extract_sources(source_files: Sequence[str | Path | NormalizedSource]) -> list[NormalizedSource]:
    sources: list[NormalizedSource] = []
    for source_file in source_files:
        if isinstance(source_file, NormalizedSource):
            sources.append(source_file)
            continue
        try:
            sources.append(extract_file(source_file))
        except ExtractionError as error:
            raise ProductIntelligencePipelineError(
                f"Source extraction failed for '{source_file}': {error}"
            ) from error
    return sources


def _source_summary(source: NormalizedSource) -> SourceSummary:
    return SourceSummary(
        source_id=source.source_id,
        source_type=source.source_type,
        source_name=source.source_name,
        locations=[location.label for location in source.locations],
    )
