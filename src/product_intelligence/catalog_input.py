"""Typed adapters for the six-column catalogue input CSV."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


INPUT_COLUMNS = (
    "Mfg_Part_Num",
    "Part_Desc",
    "E1_Brand",
    "Unilog_Brand",
    "DIB_Brand",
    "Part_Manuf",
)

PLACEHOLDER_BRAND_VALUES = frozenset(
    {
        "-- unbranded --",
        "-- no unilog brand --",
        "-- no dib brand --",
    }
)


class CatalogInputError(ValueError):
    """Raised when the catalogue input CSV does not match its contract."""


class CatalogInputRow(BaseModel):
    """One raw catalogue row; values are intentionally preserved exactly."""

    model_config = ConfigDict(extra="forbid")

    Mfg_Part_Num: str = Field(min_length=1)
    Part_Desc: str
    E1_Brand: str
    Unilog_Brand: str
    DIB_Brand: str
    Part_Manuf: str

    def raw_fields(self) -> dict[str, str]:
        """Return the six original fields without placeholder rewriting."""
        return self.model_dump()

    def brand_candidates(self) -> dict[str, str | None]:
        """Return normalized candidates while leaving raw fields unchanged."""
        return {
            field: brand_candidate(getattr(self, field))
            for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand")
        }


def load_catalog_rows(csv_path: str | Path) -> list[CatalogInputRow]:
    """Load and validate all rows from the six-column input CSV."""
    path = Path(csv_path)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            _validate_header(reader.fieldnames, INPUT_COLUMNS, path)
            rows = []
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise CatalogInputError(f"Unexpected extra fields on CSV row {row_number}.")
                values = {column: row.get(column, "") or "" for column in INPUT_COLUMNS}
                try:
                    rows.append(CatalogInputRow.model_validate(values))
                except ValueError as error:
                    raise CatalogInputError(f"Invalid catalogue row {row_number}.") from error
            return rows
    except OSError as error:
        raise CatalogInputError(f"Could not read catalogue input '{path}'.") from error


def select_catalog_row(
    rows: Iterable[CatalogInputRow],
    mfg_part_num: str,
) -> CatalogInputRow:
    """Select one row by its manufacturer part number."""
    matches = [row for row in rows if row.Mfg_Part_Num == mfg_part_num]
    if not matches:
        raise CatalogInputError(f"Manufacturer part number '{mfg_part_num}' was not found.")
    if len(matches) > 1:
        raise CatalogInputError(f"Manufacturer part number '{mfg_part_num}' is not unique.")
    return matches[0]


def is_placeholder_brand(value: str) -> bool:
    """Return whether a brand field is an explicit catalogue placeholder."""
    return value.strip().casefold() in PLACEHOLDER_BRAND_VALUES


def brand_candidate(value: str) -> str | None:
    """Convert a placeholder brand to a missing candidate, not a raw value."""
    return None if is_placeholder_brand(value) else value.strip() or None


def _validate_header(
    actual: list[str] | None,
    expected: tuple[str, ...],
    path: Path,
) -> None:
    if actual != list(expected):
        raise CatalogInputError(
            f"Unexpected header in '{path}'. Expected {list(expected)}, got {actual}."
        )
