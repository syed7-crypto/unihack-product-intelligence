"""Explainable, deterministic confidence scoring for validated attributes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .cross_source_validation import CrossSourceValidationResult, ValidatedAttribute


# The weights are intentionally small and additive so each score is easy to
# explain. They sum to 1.00 for a strong, consistent, multi-source attribute.
EVIDENCE_AVAILABLE_WEIGHT = 0.30
DIRECT_QUOTE_WEIGHT = 0.20
LOCATION_WEIGHT = 0.05
ONE_SOURCE_WEIGHT = 0.15
MULTIPLE_SOURCES_WEIGHT = 0.25
CONSISTENT_ADJUSTMENT = 0.10
CONFLICT_ADJUSTMENT = -0.45


class ConfidenceAssessment(BaseModel):
    """Confidence score and human-readable explanation for one attribute."""

    name: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    level: Literal["high", "medium", "low"]
    reasons: list[str] = Field(min_length=1)


class ConfidenceScoringResult(BaseModel):
    """Confidence assessments in the same attribute-oriented pipeline shape."""

    attributes: list[ConfidenceAssessment] = Field(min_length=1)


def calculate_confidence(
    validation_result: CrossSourceValidationResult,
) -> ConfidenceScoringResult:
    """Calculate confidence from observable validation and evidence signals.

    No model output or external knowledge is used. Conflicts are deliberately
    scored low; this function never chooses which source is correct.
    """
    assessments = [_score_attribute(attribute) for attribute in validation_result.attributes]
    return ConfidenceScoringResult(attributes=assessments)


def _score_attribute(attribute: ValidatedAttribute) -> ConfidenceAssessment:
    if attribute.status == "not_found":
        return ConfidenceAssessment(
            name=attribute.name,
            score=0.0,
            level="low",
            reasons=["No source provided a value", "Confidence is zero because the attribute was not found"],
        )

    source_count = len({item.evidence.source_id for item in attribute.values})
    has_quote = any(item.evidence.quote.strip() for item in attribute.values)
    has_location = any(item.evidence.location for item in attribute.values)

    score = EVIDENCE_AVAILABLE_WEIGHT
    reasons = ["Evidence was preserved for the extracted value"]

    if has_quote:
        score += DIRECT_QUOTE_WEIGHT
        reasons.append("Direct supporting quotes were found")
    if has_location:
        score += LOCATION_WEIGHT
        reasons.append("Source location metadata was available")

    if source_count >= 2:
        score += MULTIPLE_SOURCES_WEIGHT
        reasons.append(f"{source_count} sources provide the attribute")
    else:
        score += ONE_SOURCE_WEIGHT
        reasons.append("Only one source provides the attribute")

    if attribute.status == "consistent":
        score += CONSISTENT_ADJUSTMENT
        reasons.append("Multiple sources agree")
        reasons.append("No cross-source conflict was detected")
    elif attribute.status == "single_source":
        reasons.append("Cross-source agreement could not be established")
    else:  # conflict
        score += CONFLICT_ADJUSTMENT
        reasons.append("Sources provide different values")
        reasons.append("Conflict requires review")

    bounded_score = round(max(0.0, min(1.0, score)), 2)
    return ConfidenceAssessment(
        name=attribute.name,
        score=bounded_score,
        level=_confidence_level(bounded_score),
        reasons=reasons,
    )


def _confidence_level(score: float) -> Literal["high", "medium", "low"]:
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"
