"""Deterministic validation and conflict detection across product sources."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .attribute_extraction import AttributeEvidence, AttributeExtractionResult
from .product_identification import ProductIdentificationResult
from .unit_normalization import measurements_equivalent


class SourceAttributeValue(BaseModel):
    """A value and the evidence for it from one source."""

    model_config = ConfigDict(extra="ignore")

    value: str = Field(min_length=1)
    evidence: AttributeEvidence


class ConflictInfo(BaseModel):
    """Details explaining why an attribute is marked as conflicting."""

    distinct_values: list[str] = Field(min_length=2)
    reason: str = "Sources provide different values after safe normalization."


class ValidatedAttribute(BaseModel):
    """Cross-source state for one expected product attribute."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    values: list[SourceAttributeValue] = Field(default_factory=list)
    status: Literal["consistent", "conflict", "single_source", "not_found"]
    conflict: ConflictInfo | None = None


class CrossSourceValidationResult(BaseModel):
    """Validated status for every attribute in the dynamic product schema."""

    model_config = ConfigDict(extra="ignore")

    attributes: list[ValidatedAttribute] = Field(min_length=1)


class CrossSourceValidationError(ValueError):
    """Raised when cross-source inputs cannot be compared safely."""


def validate_cross_source(
    extracted_sources: Sequence[AttributeExtractionResult],
    product_identification: ProductIdentificationResult,
) -> CrossSourceValidationResult:
    """Compare source-confirmed values without choosing a winning source.

    ``not_found`` entries from an individual source are ignored for comparison,
    while expected attributes absent from every source remain explicitly present
    in the result as ``not_found``.
    """
    if not extracted_sources:
        raise CrossSourceValidationError("At least one extracted source is required.")

    expected_names = [attribute.name for attribute in product_identification.attributes]
    by_name: dict[str, list[SourceAttributeValue]] = {name: [] for name in expected_names}

    for extraction in extracted_sources:
        for attribute in extraction.attributes:
            if attribute.name not in by_name:
                raise CrossSourceValidationError(
                    f"Attribute '{attribute.name}' is not part of the product schema."
                )
            if attribute.status == "found":
                # AttributeExtractionResult already guarantees both fields here.
                if attribute.value is None or attribute.evidence is None:
                    raise CrossSourceValidationError(
                        f"Found attribute '{attribute.name}' is missing value or evidence."
                    )
                by_name[attribute.name].append(
                    SourceAttributeValue(value=attribute.value, evidence=attribute.evidence)
                )

    attributes = [
        _validate_attribute(name, values)
        for name, values in by_name.items()
    ]
    return CrossSourceValidationResult(attributes=attributes)


def _validate_attribute(
    name: str,
    values: list[SourceAttributeValue],
) -> ValidatedAttribute:
    if not values:
        return ValidatedAttribute(name=name, status="not_found")
    if len(values) == 1:
        return ValidatedAttribute(name=name, values=values, status="single_source")

    if _all_values_equivalent(values):
        return ValidatedAttribute(name=name, values=values, status="consistent")

    normalized_to_original: dict[str, str] = {}
    for source_value in values:
        normalized_to_original.setdefault(
            normalize_for_comparison(source_value.value), source_value.value
        )

    return ValidatedAttribute(
        name=name,
        values=values,
        status="conflict",
        conflict=ConflictInfo(distinct_values=list(normalized_to_original.values())),
    )


def _all_values_equivalent(values: list[SourceAttributeValue]) -> bool:
    """Use unit comparison when possible, otherwise retain old string behavior."""
    first_value = values[0].value
    for source_value in values[1:]:
        unit_result = measurements_equivalent(first_value, source_value.value)
        if unit_result is False:
            return False
        if unit_result is None and normalize_for_comparison(first_value) != normalize_for_comparison(source_value.value):
            return False
    return True


def normalize_for_comparison(value: str) -> str:
    """Normalize only case and whitespace for safe deterministic comparison."""
    return " ".join(value.split()).casefold()


def attribute_names(
    product_identification: ProductIdentificationResult,
) -> Iterable[str]:
    """Return schema names in their declared order for callers building views."""
    return (attribute.name for attribute in product_identification.attributes)
