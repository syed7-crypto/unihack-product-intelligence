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


class AttributeExtractionResult(BaseModel):
    """Validated values for all attributes in a product identification result."""

    model_config = ConfigDict(extra="ignore")

    attributes: list[ExtractedAttribute] = Field(min_length=1)


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
            AttributeExtractionResult,
        )
        result = AttributeExtractionResult.model_validate_json(raw_response)
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

    for attribute in result.attributes:
        if attribute.status != "found":
            continue
        assert attribute.evidence is not None  # enforced by the Pydantic model
        evidence = attribute.evidence
        if evidence.source_id != source.source_id or evidence.source_name != source.source_name:
            raise ValueError(f"Evidence for '{attribute.name}' references another source.")
        if not _quote_occurs_in_source(evidence.quote, source.extracted_text):
            raise ValueError(f"Evidence quote for '{attribute.name}' is not in the source.")
        if evidence.location and source.source_type == "pdf":
            known_locations = {location.label for location in source.locations}
            if evidence.location not in known_locations:
                raise ValueError(f"Unknown PDF location '{evidence.location}'.")


def _quote_occurs_in_source(quote: str, source_text: str) -> bool:
    """Allow harmless whitespace differences while requiring source support."""
    normalize = lambda text: re.sub(r"\s+", " ", text).strip().casefold()
    return normalize(quote) in normalize(source_text)


def _build_prompt(
    source: NormalizedSource,
    product_identification: ProductIdentificationResult,
) -> str:
    attributes = "\n".join(
        f"- {attribute.name}: {attribute.label} ({attribute.description or attribute.data_type})"
        for attribute in product_identification.attributes
    )
    locations = ", ".join(location.label for location in source.locations) or "not available"
    return f"""Extract actual product attribute values from the supplied source.

Return only the structured JSON response requested by the response schema.
Use only information present in the supplied source. Do not use outside knowledge.
Do not guess, infer, enrich, or invent values. Return status \"not_found\" with
value null and evidence null when an attribute is absent or unsupported.
Every \"found\" value MUST have evidence with the exact source_id and source_name,
a short supporting quote copied from the source, and a source location when available.
For PDF sources, preserve the page location whenever possible.

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
