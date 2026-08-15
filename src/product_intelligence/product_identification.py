"""Identify a product type and generate its attribute definitions."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .extraction import NormalizedSource
from .gemini_client import GeminiClient, create_gemini_client


class AttributeDefinition(BaseModel):
    """A relevant attribute name and metadata, without an extracted value."""

    # Gemini's response-schema dialect does not accept JSON Schema's
    # additionalProperties keyword, so allow and ignore unknown metadata.
    model_config = ConfigDict(extra="ignore")

    name: str = Field(
        min_length=1,
        pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$",
        description="Stable lower_snake_case machine-readable attribute name.",
    )
    label: str = Field(min_length=1, description="Human-readable attribute label.")
    data_type: str = Field(
        default="string",
        min_length=1,
        description="Expected value kind, such as string, number, boolean, or enum.",
    )
    unit: str | None = None
    required: bool = False
    description: str = ""


class ProductIdentificationResult(BaseModel):
    """Validated product classification and dynamic attribute schema."""

    model_config = ConfigDict(extra="ignore")

    product_type: str = Field(min_length=1)
    product_category: str = Field(min_length=1)
    attributes: list[AttributeDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def attributes_have_unique_names(self) -> "ProductIdentificationResult":
        names = [attribute.name for attribute in self.attributes]
        if len(names) != len(set(names)):
            raise ValueError("Attribute names must be unique.")
        return self


class ProductIdentificationError(RuntimeError):
    """Raised when product identification cannot produce a valid result."""


class StructuredGeminiClient(Protocol):
    """Minimal client interface used by this stage, useful for deterministic tests."""

    def generate_structured_json(
        self,
        prompt: str,
        response_schema: type[BaseModel],
    ) -> str:
        ...


def identify_product(
    source: NormalizedSource,
    client: StructuredGeminiClient | None = None,
) -> ProductIdentificationResult:
    """Identify a product and define relevant attributes, without extracting values."""
    gemini_client = client or create_gemini_client()
    prompt = _build_prompt(source)

    try:
        raw_response = gemini_client.generate_structured_json(
            prompt,
            ProductIdentificationResult,
        )
        return ProductIdentificationResult.model_validate_json(raw_response)
    except ValidationError as error:
        raise ProductIdentificationError(
            "Gemini returned a product schema that failed validation."
        ) from error
    except (TypeError, ValueError, RuntimeError) as error:
        raise ProductIdentificationError(
            "Gemini did not return a valid product identification response."
        ) from error
    except Exception as error:
        raise ProductIdentificationError(
            "Gemini product identification request failed."
        ) from error


def _build_prompt(source: NormalizedSource) -> str:
    return f"""You identify product types and define dynamic attribute schemas.

Analyze the source below and return only the structured JSON response requested by
the response schema. Determine what kind of product is described and what
attributes should exist for that product type.

Do not extract actual attribute values from the source.
Do not include values, measurements, specifications, evidence, confidence scores,
missing fields, conflicts, or enrichment suggestions.
Attribute definitions should use stable lower_snake_case names and useful labels.

Source name: {source.source_name}
Source type: {source.source_type}
Source text:
---
{source.extracted_text}
---
"""
