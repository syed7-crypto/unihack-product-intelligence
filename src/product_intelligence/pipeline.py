"""End-to-end orchestration for the product intelligence pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
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
from .diagnostics import Diagnostic
from .extraction import ExtractionError, NormalizedSource, extract_file
from .gemini_client import create_gemini_client
from .product_identification import (
    AttributeDefinition,
    ProductIdentificationError,
    ProductIdentificationResult,
    identify_product,
)
from .runtime_timing import RuntimeTimingAccumulator


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
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class ProductIntelligencePipelineError(RuntimeError):
    """Raised when a pipeline stage fails with an actionable stage name."""

    def __init__(self, message: str, code: str = "PIPELINE_FAILED", diagnostics=None) -> None:
        self.code = code
        self.diagnostics = list(diagnostics or ())
        super().__init__(message)


def run_pipeline(
    source_files: Sequence[str | Path | NormalizedSource],
    client: StructuredGeminiClient | None = None,
    *,
    runtime_timing: RuntimeTimingAccumulator | None = None,
    attribute_extraction_concurrency: int = 1,
) -> ProductIntelligenceResult:
    """Run extraction, AI stages, validation, and scoring for source files.

    The product is identified once from the first source. Its resulting dynamic
    schema is then applied consistently to every source in ``source_files``.
    Pass a mocked structured client in tests to avoid live Gemini calls.
    """
    if not source_files:
        raise ProductIntelligencePipelineError("At least one source file is required.")
    if attribute_extraction_concurrency < 1:
        raise ValueError("attribute extraction concurrency must be positive.")

    sources = _extract_sources(source_files)
    gemini_client = client
    try:
        gemini_client = gemini_client or create_gemini_client()
        if runtime_timing is None:
            product_identification = identify_product(sources[0], gemini_client)
        else:
            with runtime_timing.measure(
                "product_identification_duration_seconds",
                "product_identification_calls",
            ):
                product_identification = identify_product(sources[0], gemini_client)
    except ProductIdentificationError as error:
        category = _product_identification_failure_category(error)
        provider_suffix = (
            f" Provider category: {error.provider_category}."
            if error.provider_category else ""
        )
        raise ProductIntelligencePipelineError(
            "Product identification failed: "
            f"{category}.",
            "PRODUCT_IDENTIFICATION_FAILED",
            diagnostics=[
                Diagnostic(
                    code=category.upper(),
                    message=str(error) + provider_suffix,
                )
            ],
        ) from error
    except Exception as error:
        raise ProductIntelligencePipelineError(
            "Could not initialize the product intelligence pipeline."
        ) from error

    extraction_outcomes = _extract_attributes_in_order(
        sources,
        product_identification,
        gemini_client,
        runtime_timing=runtime_timing,
        concurrency=attribute_extraction_concurrency,
    )
    extracted_attributes: list[AttributeExtractionResult] = []
    diagnostics: list[Diagnostic] = []
    first_extraction_error: AttributeExtractionError | None = None
    for source, outcome in zip(sources, extraction_outcomes):
        if isinstance(outcome, AttributeExtractionError):
            error = outcome
            if first_extraction_error is None:
                first_extraction_error = error
            diagnostics.append(
                Diagnostic(
                    code=error.code,
                    message=(
                        error.message
                        + (
                            f" Provider category: {error.provider_category}."
                            if error.provider_category else ""
                        )
                    ),
                    source_id=source.source_id,
                    source_name=source.source_name,
                )
            )
            continue
        if isinstance(outcome, Exception):
            raise outcome
        extracted_attributes.append(outcome)
    if not extracted_attributes:
        if first_extraction_error is not None:
            raise ProductIntelligencePipelineError(
                "Attribute value extraction failed: "
                f"{first_extraction_error.code}. {first_extraction_error.message}",
                "ATTRIBUTE_EXTRACTION_FAILED",
                diagnostics,
            ) from first_extraction_error
        raise ProductIntelligencePipelineError(
            "Attribute value extraction failed.",
            "ATTRIBUTE_EXTRACTION_FAILED",
            diagnostics,
        )

    try:
        if runtime_timing is None:
            validation = validate_cross_source(
                extracted_attributes, product_identification
            )
        else:
            with runtime_timing.measure(
                "validation_delivery_mapping_duration_seconds"
            ):
                validation = validate_cross_source(
                    extracted_attributes, product_identification
                )
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
        diagnostics=diagnostics,
    )


def _extract_attributes_in_order(
    sources: Sequence[NormalizedSource],
    product_identification: ProductIdentificationResult,
    gemini_client: StructuredGeminiClient,
    *,
    runtime_timing: RuntimeTimingAccumulator | None,
    concurrency: int,
) -> list[AttributeExtractionResult | Exception]:
    """Run independent source requests while returning outcomes by source index."""
    if concurrency == 1 or len(sources) <= 1:
        return [
            _extract_one_source(
                source,
                product_identification,
                gemini_client,
                runtime_timing=runtime_timing,
            )
            for source in sources
        ]

    with ThreadPoolExecutor(
        max_workers=min(concurrency, len(sources)),
        thread_name_prefix="unihack-gemini-extraction",
    ) as executor:
        futures = [
            executor.submit(
                _extract_one_source,
                source,
                product_identification,
                gemini_client,
                runtime_timing=runtime_timing,
            )
            for source in sources
        ]
        # Reading futures in submission order preserves the old source order;
        # the executor still waits for every independent request to finish.
        return [future.result() for future in futures]


def _extract_one_source(
    source: NormalizedSource,
    product_identification: ProductIdentificationResult,
    gemini_client: StructuredGeminiClient,
    *,
    runtime_timing: RuntimeTimingAccumulator | None,
) -> AttributeExtractionResult | Exception:
    try:
        if runtime_timing is None:
            return extract_attribute_values(source, product_identification, gemini_client)
        with runtime_timing.measure(
            "attribute_extraction_duration_seconds",
            "attribute_extraction_calls",
        ):
            return extract_attribute_values(source, product_identification, gemini_client)
    except Exception as error:
        return error


def _product_identification_failure_category(error: ProductIdentificationError) -> str:
    """Return a bounded category without exposing response or exception text."""
    message = str(error).casefold()
    if "schema" in message and "validation" in message:
        return "schema_validation"
    if "valid product identification response" in message:
        return "response_invalid_or_runtime"
    if "request failed" in message:
        return "gemini_request_failure"
    return "product_identification_failure"


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
