"""Deterministic, governed attribute-to-delivery mapping infrastructure.

This module contains configuration models and resolution only.  It does not
contain production UniHack mappings or LOV/UOM data.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .reference_data import normalize_reference_value


ConfidenceLevel = Literal["low", "medium", "high"]
MappingSource = Literal["mock", "official"]
GovernanceStatus = Literal["candidate", "approved"]
DeliveryPolicy = Literal["preserve"]


class ControlledAttributeMapping(BaseModel):
    """One reusable controlled attribute mapping.

    ``internal_attribute_name`` and the legacy fields are retained for
    backwards compatibility.  New configurations should set
    ``canonical_attribute_name`` explicitly.
    """

    internal_attribute_name: str = Field(min_length=1)
    canonical_attribute_name: str | None = None
    delivery_label: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    applicable_categories: tuple[str, ...] = ()
    data_type: str | None = None
    allowed_values_reference: str | None = None
    allowed_uom_reference: str | None = None
    # Slots are retained as optional legacy metadata.  The MVP delivery
    # exporter assigns slots sequentially; this field no longer governs
    # delivery position.
    slot: int | None = Field(default=None, ge=1, le=50)
    # Existing names remain supported by the enrichment mapper.
    reference_attribute: str | None = None
    expected_uom: str | None = None
    delivery_value_policy: DeliveryPolicy = "preserve"
    delivery_uom_policy: DeliveryPolicy = "preserve"
    minimum_confidence_level: ConfidenceLevel = "medium"
    mapping_source: MappingSource = "mock"
    governance_status: GovernanceStatus = "candidate"
    governance_reason: str = Field(
        default="Configuration is not official until explicitly governed.",
        min_length=1,
    )

    @property
    def canonical_name(self) -> str:
        return self.canonical_attribute_name or self.internal_attribute_name

    def matches(self, extracted_name: str, category: str | None = None) -> bool:
        """Return whether an extracted name matches this explicit mapping."""
        if category and self.applicable_categories:
            allowed = {normalize_reference_value(item) for item in self.applicable_categories}
            if normalize_reference_value(category) not in allowed:
                return False
        candidate = normalize_reference_value(extracted_name)
        names = {normalize_reference_value(self.canonical_name)}
        names.update(normalize_reference_value(alias) for alias in self.aliases)
        # The legacy internal name is also an explicit accepted name.
        names.add(normalize_reference_value(self.internal_attribute_name))
        return candidate in names

    @property
    def value_reference_name(self) -> str:
        return self.allowed_values_reference or self.reference_attribute or self.canonical_name

    @property
    def uom_reference_name(self) -> str | None:
        return self.allowed_uom_reference or self.expected_uom


class ControlledAttributeMappingRegistry(BaseModel):
    """Validated collection of reusable, non-ambiguous mappings."""

    mappings: list[ControlledAttributeMapping] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_mappings(self) -> "ControlledAttributeMappingRegistry":
        slots = [mapping.slot for mapping in self.mappings if mapping.slot is not None]
        if len(slots) != len(set(slots)):
            raise ValueError("Delivery attribute slots must be unique.")

        canonical_names = [normalize_reference_value(mapping.canonical_name) for mapping in self.mappings]
        if len(canonical_names) != len(set(canonical_names)):
            raise ValueError("Canonical attribute mappings must be unique.")

        owners: dict[str, str] = {}
        for mapping in self.mappings:
            accepted = [mapping.canonical_name, mapping.internal_attribute_name, *mapping.aliases]
            for name in accepted:
                key = normalize_reference_value(name)
                owner = owners.get(key)
                if owner is not None and owner != mapping.canonical_name:
                    raise ValueError(f"Ambiguous controlled attribute alias: '{name}'.")
                owners[key] = mapping.canonical_name
        return self

    def resolve(
        self,
        extracted_name: str,
        *,
        category: str | None = None,
    ) -> ControlledAttributeMapping | None:
        """Resolve only an exact canonical name or explicitly configured alias."""
        for mapping in self.mappings:
            if mapping.matches(extracted_name, category):
                return mapping
        return None


def resolve_controlled_attribute_mapping(
    extracted_name: str,
    mappings: Sequence[ControlledAttributeMapping],
    *,
    category: str | None = None,
) -> ControlledAttributeMapping | None:
    """Resolve against a sequence without adding implicit/fuzzy behavior."""
    registry = ControlledAttributeMappingRegistry(mappings=list(mappings))
    return registry.resolve(extracted_name, category=category)
