"""Delivery-format schema loaded from the supplied expected-output CSV."""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from pathlib import Path


class DeliverySchemaError(ValueError):
    """Raised when the delivery CSV schema or row shape is invalid."""


EXPECTED_DELIVERY_COLUMN_COUNT = 252


class DeliverySchema:
    """Ordered, exact delivery columns from the expected-output header."""

    def __init__(self, columns: Sequence[str]) -> None:
        columns_tuple = tuple(columns)
        if not columns_tuple:
            raise DeliverySchemaError("The delivery schema must contain columns.")
        if any(not column for column in columns_tuple):
            raise DeliverySchemaError("Delivery columns must not be empty.")
        if len(set(columns_tuple)) != len(columns_tuple):
            raise DeliverySchemaError("Delivery columns must be unique.")
        self.columns = columns_tuple

    def empty_row(self) -> dict[str, str]:
        """Create an ordered blank row with exactly the schema columns."""
        return {column: "" for column in self.columns}

    def validate_row(self, row: Mapping[str, str]) -> dict[str, str]:
        """Validate exact keys and return an ordered copy of the row."""
        actual_columns = list(row.keys())
        if actual_columns != list(self.columns):
            raise DeliverySchemaError(
                "Delivery row columns do not exactly match the expected schema order."
            )
        if any(not isinstance(value, str) for value in row.values()):
            raise DeliverySchemaError("Delivery row values must be strings.")
        return {column: row[column] for column in self.columns}


def load_delivery_schema(csv_path: str | Path) -> DeliverySchema:
    """Load the exact ordered header from the supplied delivery CSV."""
    path = Path(csv_path)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
    except (OSError, csv.Error) as error:
        raise DeliverySchemaError(f"Could not read delivery schema '{path}'.") from error
    if header is None:
        raise DeliverySchemaError(f"Delivery schema '{path}' is empty.")
    if len(header) != EXPECTED_DELIVERY_COLUMN_COUNT:
        raise DeliverySchemaError(
            f"Expected {EXPECTED_DELIVERY_COLUMN_COUNT} delivery columns, got {len(header)}."
        )
    return DeliverySchema(header)


def load_delivery_rows(
    csv_path: str | Path,
    schema: DeliverySchema | None = None,
) -> list[dict[str, str]]:
    """Load delivery rows and validate their columns against the header."""
    path = Path(csv_path)
    schema = schema or load_delivery_schema(path)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(schema.columns):
                raise DeliverySchemaError("Delivery CSV header changed after schema loading.")
            return [schema.validate_row(row) for row in reader]
    except OSError as error:
        raise DeliverySchemaError(f"Could not read delivery rows '{path}'.") from error


def select_delivery_row(
    rows: list[dict[str, str]],
    mfg_part_num: str,
) -> dict[str, str]:
    """Select a known-good delivery row by its preserved input part number."""
    matches = [row for row in rows if row.get("Mfg_Part_Num") == mfg_part_num]
    if not matches:
        raise DeliverySchemaError(f"Delivery row '{mfg_part_num}' was not found.")
    if len(matches) > 1:
        raise DeliverySchemaError(f"Delivery row '{mfg_part_num}' is not unique.")
    return matches[0]
