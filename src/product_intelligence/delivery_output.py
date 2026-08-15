"""Deterministic delivery-row mapping and comparison helpers."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, Field

from .catalog_input import CatalogInputRow
from .delivery_schema import DeliverySchema, DeliverySchemaError


class DeliveryFieldDifference(BaseModel):
    """One exact field difference between generated and known-good rows."""

    field: str
    generated: str
    expected: str


class DeliveryComparison(BaseModel):
    """Deterministic comparison summary for one catalogue row."""

    mfg_part_num: str
    matches: bool
    differences: list[DeliveryFieldDifference] = Field(default_factory=list)


def map_raw_fields_to_delivery(
    input_row: CatalogInputRow,
    schema: DeliverySchema,
) -> dict[str, str]:
    """Copy only the six raw fields that also exist in the delivery schema."""
    delivery_row = schema.empty_row()
    for field, value in input_row.raw_fields().items():
        if field not in delivery_row:
            raise DeliverySchemaError(f"Delivery schema is missing raw field '{field}'.")
        delivery_row[field] = value
    return schema.validate_row(delivery_row)


def compare_delivery_rows(
    generated: Mapping[str, str],
    expected: Mapping[str, str],
    schema: DeliverySchema,
) -> DeliveryComparison:
    """Compare every delivery field in schema order without modifying either row."""
    generated_row = schema.validate_row(generated)
    expected_row = schema.validate_row(expected)
    differences = [
        DeliveryFieldDifference(
            field=column,
            generated=generated_row[column],
            expected=expected_row[column],
        )
        for column in schema.columns
        if generated_row[column] != expected_row[column]
    ]
    return DeliveryComparison(
        mfg_part_num=generated_row.get("Mfg_Part_Num", ""),
        matches=not differences,
        differences=differences,
    )
