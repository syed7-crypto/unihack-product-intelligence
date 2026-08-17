"""Extract source-backed values for a product's dynamic attributes."""

from __future__ import annotations

import re
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .extraction import NormalizedSource
from .gemini_client import create_gemini_client
from .product_identification import ProductIdentificationResult


class AttributeEvidence(BaseModel):
    """Reference to the source text supporting an extracted value."""

    model_config = ConfigDict(extra="ignore")

    source_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    location: str | None = None
    quote: str = Field(min_length=1)


class ExtractedAttribute(BaseModel):
    """One dynamic attribute and its source-backed extraction status."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    value: str | None = None
    status: Literal["found", "not_found"]
    evidence: AttributeEvidence | None = None

    @model_validator(mode="after")
    def validate_status_fields(self) -> "ExtractedAttribute":
        if self.status == "found" and (self.value is None or self.evidence is None):
            raise ValueError("Found attributes require both a value and evidence.")
        if self.status == "not_found" and (self.value is not None or self.evidence is not None):
            raise ValueError("Not-found attributes must not contain a value or evidence.")
        return self


class RejectedAttribute(BaseModel):
    """A proposed value rejected by deterministic evidence validation."""

    name: str = Field(min_length=1)
    code: Literal[
        "EVIDENCE_MISSING",
        "EVIDENCE_SOURCE_MISMATCH",
        "EVIDENCE_QUOTE_NOT_FOUND",
        "EVIDENCE_VALUE_NOT_IN_QUOTE",
        "EVIDENCE_LOCATION_INVALID",
    ]
    message: str = Field(min_length=1)
    proposed_value: str | None = None


class AttributeExtractionResult(BaseModel):
    """Validated values for all attributes in a product identification result."""

    model_config = ConfigDict(extra="ignore")

    attributes: list[ExtractedAttribute] = Field(min_length=1)
    rejected_attributes: list[RejectedAttribute] = Field(default_factory=list)


class GeminiAttributeResponse(BaseModel):
    """Flat Gemini-facing attribute shape with all response keys required."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    status: Literal["found", "not_found"]
    value: str | None = Field(...)
    evidence: AttributeEvidence | None = Field(...)


class GeminiAttributeExtractionResult(BaseModel):
    """Gemini-compatible response schema; semantic rules are checked afterward."""

    model_config = ConfigDict(extra="ignore")

    attributes: list[GeminiAttributeResponse] = Field(min_length=1)
    rejected_attributes: list[RejectedAttribute] = Field(default_factory=list)


class AttributeExtractionError(RuntimeError):
    """Raised when Gemini output is malformed or unsupported by the source."""


class StructuredGeminiClient(Protocol):
    """Minimal Gemini interface used by this stage and its unit tests."""

    def generate_structured_json(
        self,
        prompt: str,
        response_schema: type[BaseModel],
    ) -> str:
        ...


def extract_attribute_values(
    source: NormalizedSource,
    product_identification: ProductIdentificationResult,
    client: StructuredGeminiClient | None = None,
) -> AttributeExtractionResult:
    """Extract only values explicitly supported by ``source``.

    Product identification supplies the allowed attribute names; it does not
    perform extraction itself. Python then applies source/evidence checks to the
    structured response returned by Gemini.
    """
    gemini_client = client or create_gemini_client()

    try:
        raw_response = gemini_client.generate_structured_json(
            _build_prompt(source, product_identification),
            GeminiAttributeExtractionResult,
        )
        response = GeminiAttributeExtractionResult.model_validate_json(raw_response)
        result = AttributeExtractionResult.model_validate(response.model_dump())
        _validate_against_input(result, source, product_identification)
        return result
    except ValidationError as error:
        raise AttributeExtractionError(
            "Gemini returned attribute values that failed validation."
        ) from error
    except (TypeError, ValueError, RuntimeError) as error:
        raise AttributeExtractionError(
            "Gemini did not return valid source-backed attribute values."
        ) from error
    except Exception as error:
        raise AttributeExtractionError("Gemini attribute extraction request failed.") from error


def _validate_against_input(
    result: AttributeExtractionResult,
    source: NormalizedSource,
    product_identification: ProductIdentificationResult,
) -> None:
    expected_names = {attribute.name for attribute in product_identification.attributes}
    actual_names = [attribute.name for attribute in result.attributes]

    if len(actual_names) != len(set(actual_names)):
        raise ValueError("Gemini returned duplicate attribute names.")
    if set(actual_names) != expected_names:
        raise ValueError("Gemini must return exactly the identified attributes.")

    accepted: list[ExtractedAttribute] = []
    rejected: list[RejectedAttribute] = []
    for attribute in result.attributes:
        if attribute.status != "found":
            accepted.append(attribute)
            continue
        assert attribute.evidence is not None  # enforced by the Pydantic model
        evidence = attribute.evidence
        if evidence.source_id != source.source_id or evidence.source_name != source.source_name:
            rejected.append(_rejected(attribute, "EVIDENCE_SOURCE_MISMATCH", "Evidence references another source."))
            accepted.append(_not_found(attribute))
            continue
        if not _quote_occurs_in_source(evidence.quote, source.extracted_text):
            rejected.append(_rejected(attribute, "EVIDENCE_QUOTE_NOT_FOUND", "Evidence quote is not in the source."))
            accepted.append(_not_found(attribute))
            continue
        if not attribute.value.strip() or not _quote_occurs_in_source(attribute.value, evidence.quote):
            rejected.append(_rejected(attribute, "EVIDENCE_VALUE_NOT_IN_QUOTE", "The value is not supported by its evidence quote."))
            accepted.append(_not_found(attribute))
            continue
        if evidence.location:
            if source.source_type == "web":
                canonical_location = _canonical_web_location(evidence.location, source)
            else:
                canonical_location = _canonical_location(evidence.location, source)
                if canonical_location is None:
                    rejected.append(_rejected(attribute, "EVIDENCE_LOCATION_INVALID", f"Unknown source location '{evidence.location}'."))
                    accepted.append(_not_found(attribute))
                    continue
            attribute = attribute.model_copy(
                update={
                    "evidence": evidence.model_copy(
                        update={"location": canonical_location}
                    )
                }
            )
        elif source.source_type == "web":
            # Webpage locations are secondary metadata. A valid quote from
            # general page text receives the stable document location.
            attribute = attribute.model_copy(
                update={
                    "evidence": evidence.model_copy(update={"location": "document"})
                }
            )
        accepted.append(attribute)

    result.attributes = accepted
    result.rejected_attributes = rejected


def _not_found(attribute: ExtractedAttribute) -> ExtractedAttribute:
    return attribute.model_copy(update={"value": None, "status": "not_found", "evidence": None})


def _rejected(attribute: ExtractedAttribute, code: str, message: str) -> RejectedAttribute:
    return RejectedAttribute(
        name=attribute.name,
        code=code,
        message=message,
        proposed_value=attribute.value,
    )


def _quote_occurs_in_source(quote: str, source_text: str) -> bool:
    """Allow harmless whitespace differences while requiring source support."""
    normalize = lambda text: re.sub(r"\s+", " ", text).strip().casefold()
    return normalize(quote) in normalize(source_text)


def normalize_location_label(value: str) -> str:
    """Normalize only case and whitespace for safe location matching."""
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def _canonical_location(value: str, source: NormalizedSource) -> str | None:
    requested = normalize_location_label(value)
    for location in source.locations:
        if normalize_location_label(location.label) == requested:
            return location.label
    return None


def _canonical_web_location(value: str, source: NormalizedSource) -> str:
    """Resolve known web headings; otherwise use document after quote checks."""
    return _canonical_location(value, source) or "document"


def _build_prompt(
    source: NormalizedSource,
    product_identification: ProductIdentificationResult,
) -> str:
    attributes = "\n".join(
        f"- {attribute.name}: {attribute.label} ({attribute.description or attribute.data_type})"
        for attribute in product_identification.attributes
    )
    locations = ", ".join(location.label for location in source.locations) or "not available"
    webpage_location_rules = ""
    if source.source_type == "web":
        webpage_location_rules = """
For webpage sources, use \"document\" when the supporting quote is from general
webpage text. Use a heading location only when the evidence is specifically
associated with one of the exact supplied heading labels. If using a heading,
copy that heading label exactly as provided. Do not invent section names. The
supporting quote is authoritative; location is secondary metadata and is not
proof that the value occurs in the source.
"""

    return f"""Extract actual product attribute values from the supplied source.

Return only the structured JSON response requested by the response schema.
Every attribute object MUST contain all four keys: name, status, value, evidence.
Use only information present in the supplied source. Do not use outside knowledge.
Do not guess, infer, enrich, or invent values. Return status \"not_found\" with
value null and evidence null when an attribute is absent or unsupported.
Every \"found\" value MUST have evidence with the exact source_id and source_name,
a short supporting quote copied from the source, and a source location when available.
If status is \"found\", the value field MUST be present and non-null and evidence
MUST be present. If status is \"not_found\", value MUST be present as null and
evidence MUST be present as null. Never omit the value or evidence keys.
For PDF sources, preserve the page location whenever possible.
{webpage_location_rules}

Product type: {product_identification.product_type}
Relevant attributes:
{attributes}

Source id: {source.source_id}
Source name: {source.source_name}
Source type: {source.source_type}
Available locations: {locations}
Source text:
---
{source.extracted_text}
---
"""
