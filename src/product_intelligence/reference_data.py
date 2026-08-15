"""Deterministic boundaries for controlled catalogue reference data."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, Field

from .catalog_input import CatalogInputRow, brand_candidate, is_placeholder_brand


ResolutionStatus = Literal["resolved", "unresolved", "invalid"]


class ReferenceResolutionResult(BaseModel):
    """Outcome of a controlled reference lookup or validation."""

    input_value: str | None
    resolved_value: str | list[str] | None
    status: ResolutionStatus
    reference_type: str
    reason: str


def normalize_reference_value(value: str) -> str:
    """Normalize only whitespace and case for deterministic exact matching."""
    return " ".join(value.strip().casefold().split())


class _ExactReference:
    def __init__(self, approved_values: Iterable[str], reference_type: str) -> None:
        values = tuple(approved_values)
        if not values or any(not value.strip() for value in values):
            raise ValueError(f"{reference_type} reference requires non-empty values.")
        self.reference_type = reference_type
        self._lookup: dict[str, str] = {}
        for value in values:
            key = normalize_reference_value(value)
            if key in self._lookup and self._lookup[key] != value:
                raise ValueError(f"Ambiguous normalized {reference_type} reference: '{value}'.")
            self._lookup[key] = value

    def resolve(self, raw_value: str | None) -> ReferenceResolutionResult:
        if raw_value is None or not raw_value.strip():
            return ReferenceResolutionResult(
                input_value=raw_value,
                resolved_value=None,
                status="unresolved",
                reference_type=self.reference_type,
                reason="No candidate value was supplied.",
            )
        resolved = self._lookup.get(normalize_reference_value(raw_value))
        if resolved is None:
            return ReferenceResolutionResult(
                input_value=raw_value,
                resolved_value=None,
                status="unresolved",
                reference_type=self.reference_type,
                reason="No exact normalized match exists in the controlled reference.",
            )
        return ReferenceResolutionResult(
            input_value=raw_value,
            resolved_value=resolved,
            status="resolved",
            reference_type=self.reference_type,
            reason="Matched an approved reference value.",
        )


class ManufacturerReference(_ExactReference):
    """Exact/normalized lookup for approved manufacturer names."""

    def __init__(self, approved_values: Iterable[str]) -> None:
        super().__init__(approved_values, "manufacturer")


class BrandReference(_ExactReference):
    """Exact/normalized lookup for approved brand names."""

    def __init__(self, approved_values: Iterable[str]) -> None:
        super().__init__(approved_values, "brand")

    def resolve(self, raw_value: str | None) -> ReferenceResolutionResult:
        if raw_value is not None and is_placeholder_brand(raw_value):
            return ReferenceResolutionResult(
                input_value=raw_value,
                resolved_value=None,
                status="unresolved",
                reference_type="brand",
                reason="The input is an explicit brand placeholder.",
            )
        return super().resolve(raw_value)


class TaxonomyPath(BaseModel):
    """One approved Dept/Class/Fine/Classpath combination."""

    dept: str = Field(min_length=1)
    class_name: str = Field(min_length=1)
    fine: str = Field(min_length=1)
    classpath: str = Field(min_length=1)

    def parts(self) -> tuple[str, str, str, str]:
        return self.dept, self.class_name, self.fine, self.classpath


class TaxonomyReference:
    """Validate taxonomy values against explicitly supplied approved paths."""

    def __init__(self, approved_paths: Sequence[TaxonomyPath]) -> None:
        if not approved_paths:
            raise ValueError("Taxonomy reference requires approved paths.")
        self._lookup: dict[tuple[str, str, str, str], TaxonomyPath] = {}
        for path in approved_paths:
            key = tuple(normalize_reference_value(part) for part in path.parts())
            if key in self._lookup and self._lookup[key] != path:
                raise ValueError("Ambiguous normalized taxonomy reference.")
            self._lookup[key] = path

    def validate(
        self,
        dept: str,
        class_name: str,
        fine: str,
        classpath: str,
    ) -> ReferenceResolutionResult:
        values = (dept, class_name, fine, classpath)
        input_value = " | ".join(values)
        if any(not value.strip() for value in values):
            return ReferenceResolutionResult(
                input_value=input_value,
                resolved_value=None,
                status="invalid",
                reference_type="taxonomy",
                reason="All taxonomy levels are required for validation.",
            )
        approved = self._lookup.get(tuple(normalize_reference_value(value) for value in values))
        if approved is None:
            return ReferenceResolutionResult(
                input_value=input_value,
                resolved_value=None,
                status="invalid",
                reference_type="taxonomy",
                reason="The taxonomy path is not in the controlled reference.",
            )
        return ReferenceResolutionResult(
            input_value=input_value,
            resolved_value=" | ".join(approved.parts()),
            status="resolved",
            reference_type="taxonomy",
            reason="Matched an approved taxonomy path.",
        )


class AttributeRule(BaseModel):
    """Controlled attribute definition and its allowed values/UOMs."""

    label: str = Field(min_length=1)
    allowed_values: tuple[str, ...] = ()
    allowed_uoms: tuple[str, ...] = ()


class AttributeReference:
    """Controlled attributes, values, and UOMs grouped by category."""

    def __init__(self, categories: Mapping[str, Mapping[str, AttributeRule]]) -> None:
        self._categories: dict[str, dict[str, AttributeRule]] = {}
        for category, attributes in categories.items():
            category_key = normalize_reference_value(category)
            self._categories[category_key] = {
                normalize_reference_value(name): rule for name, rule in attributes.items()
            }

    def attributes_for(self, category: str) -> ReferenceResolutionResult:
        rules = self._categories.get(normalize_reference_value(category))
        if rules is None:
            return ReferenceResolutionResult(
                input_value=category,
                resolved_value=None,
                status="unresolved",
                reference_type="attribute",
                reason="No controlled attribute reference exists for this category.",
            )
        return ReferenceResolutionResult(
            input_value=category,
            resolved_value=[rule.label for rule in rules.values()],
            status="resolved",
            reference_type="attribute",
            reason="Returned attributes from the controlled category reference.",
        )

    def allowed_values(self, category: str, attribute: str) -> tuple[str, ...]:
        rule = self._rule(category, attribute)
        return rule.allowed_values if rule else ()

    def allowed_uoms(self, category: str, attribute: str) -> tuple[str, ...]:
        rule = self._rule(category, attribute)
        return rule.allowed_uoms if rule else ()

    def validate_value(
        self,
        category: str,
        attribute: str,
        value: str,
    ) -> ReferenceResolutionResult:
        rule = self._rule(category, attribute)
        if rule is None:
            return ReferenceResolutionResult(
                input_value=value,
                resolved_value=None,
                status="unresolved",
                reference_type="attribute_value",
                reason="No controlled reference exists for this category and attribute.",
            )
        if not rule.allowed_values:
            return ReferenceResolutionResult(
                input_value=value,
                resolved_value=None,
                status="unresolved",
                reference_type="attribute_value",
                reason="The attribute has no controlled allowed-value list.",
            )
        lookup = {normalize_reference_value(item): item for item in rule.allowed_values}
        resolved = lookup.get(normalize_reference_value(value))
        if resolved is None:
            return ReferenceResolutionResult(
                input_value=value,
                resolved_value=None,
                status="invalid",
                reference_type="attribute_value",
                reason="The value is not in the controlled allowed-value list.",
            )
        return ReferenceResolutionResult(
            input_value=value,
            resolved_value=resolved,
            status="resolved",
            reference_type="attribute_value",
            reason="Matched an approved attribute value.",
        )

    def _rule(self, category: str, attribute: str) -> AttributeRule | None:
        return self._categories.get(normalize_reference_value(category), {}).get(
            normalize_reference_value(attribute)
        )


class UOMReference(_ExactReference):
    """Approved UOM lookup with only explicitly supplied aliases."""

    def __init__(self, approved_aliases: Mapping[str, Iterable[str]]) -> None:
        canonical_values = tuple(approved_aliases)
        super().__init__(canonical_values, "uom")
        for canonical, aliases in approved_aliases.items():
            for alias in aliases:
                key = normalize_reference_value(alias)
                existing = self._lookup.get(key)
                if existing is not None and existing != canonical:
                    raise ValueError(f"Ambiguous normalized UOM alias: '{alias}'.")
                self._lookup[key] = canonical

    def resolve(self, raw_value: str | None) -> ReferenceResolutionResult:
        result = super().resolve(raw_value)
        if result.status == "unresolved" and raw_value is not None and raw_value.strip():
            return result.model_copy(
                update={
                    "status": "invalid",
                    "reason": "The UOM is not in the approved UOM reference.",
                }
            )
        return result


class CatalogReferenceResolution(BaseModel):
    """Reference outcomes for one raw catalogue row."""

    manufacturer: ReferenceResolutionResult
    brands: dict[str, ReferenceResolutionResult]


def resolve_catalog_row_references(
    row: CatalogInputRow,
    manufacturer_reference: ManufacturerReference,
    brand_reference: BrandReference,
) -> CatalogReferenceResolution:
    """Resolve raw manufacturer/brand candidates without mutating the row."""
    brand_results = {}
    for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand"):
        raw_value = getattr(row, field)
        candidate = brand_candidate(raw_value)
        result = brand_reference.resolve(candidate)
        if candidate is None:
            result = result.model_copy(update={"input_value": raw_value})
        brand_results[field] = result
    return CatalogReferenceResolution(
        manufacturer=manufacturer_reference.resolve(row.Part_Manuf),
        brands=brand_results,
    )
