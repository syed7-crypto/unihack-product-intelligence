"""Tests for the minimal catalogue input and delivery-row infrastructure."""

import unittest
from pathlib import Path

from src.product_intelligence.catalog_input import (
    INPUT_COLUMNS,
    brand_candidate,
    is_placeholder_brand,
    load_catalog_rows,
    select_catalog_row,
)
from src.product_intelligence.delivery_output import (
    compare_delivery_rows,
    map_raw_fields_to_delivery,
)
from src.product_intelligence.delivery_schema import (
    load_delivery_rows,
    load_delivery_schema,
    select_delivery_row,
)


DATA_DIRECTORY = Path(r"C:\Users\syed7\Downloads")
INPUT_CSV = DATA_DIRECTORY / "Unihack_ Sample Dataset - Input.csv"
DELIVERY_CSV = DATA_DIRECTORY / "Unihack_ Expected Output - Delivery Format.csv"
FIXTURE_PART = "PDSH4816AF"


class CatalogueAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not INPUT_CSV.exists() or not DELIVERY_CSV.exists():
            raise unittest.SkipTest("The supplied UniHack CSV files are not available.")

    def test_input_csv_loads_and_selects_fixture(self) -> None:
        rows = load_catalog_rows(INPUT_CSV)

        self.assertEqual(len(rows), 1000)
        fixture = select_catalog_row(rows, FIXTURE_PART)
        self.assertEqual(fixture.Mfg_Part_Num, FIXTURE_PART)
        self.assertEqual(tuple(fixture.raw_fields()), INPUT_COLUMNS)

    def test_placeholder_brand_values_are_missing_candidates(self) -> None:
        self.assertTrue(is_placeholder_brand("-- Unbranded --"))
        self.assertTrue(is_placeholder_brand(" -- NO UNILOG BRAND -- "))
        self.assertTrue(is_placeholder_brand("-- No DIB Brand --"))
        self.assertFalse(is_placeholder_brand("3M"))
        self.assertIsNone(brand_candidate("-- Unbranded --"))
        self.assertEqual(brand_candidate("  3M  "), "3M")

    def test_delivery_schema_comes_from_header_and_has_252_columns(self) -> None:
        schema = load_delivery_schema(DELIVERY_CSV)

        self.assertEqual(len(schema.columns), 252)
        self.assertEqual(len(schema.empty_row()), 252)
        self.assertEqual(len(set(schema.columns)), 252)

    def test_raw_fields_map_unchanged_and_do_not_map_manufacturer_name(self) -> None:
        input_row = select_catalog_row(load_catalog_rows(INPUT_CSV), FIXTURE_PART)
        schema = load_delivery_schema(DELIVERY_CSV)

        generated = map_raw_fields_to_delivery(input_row, schema)

        for field in INPUT_COLUMNS:
            self.assertEqual(generated[field], getattr(input_row, field))
        self.assertNotEqual(generated["Part_Manuf"], generated["MANUFACTURER_NAME"])
        self.assertEqual(generated["MANUFACTURER_NAME"], "")

    def test_generated_row_compares_against_known_good_fixture(self) -> None:
        input_row = select_catalog_row(load_catalog_rows(INPUT_CSV), FIXTURE_PART)
        schema = load_delivery_schema(DELIVERY_CSV)
        expected = select_delivery_row(load_delivery_rows(DELIVERY_CSV, schema), FIXTURE_PART)
        generated = map_raw_fields_to_delivery(input_row, schema)

        comparison = compare_delivery_rows(generated, expected, schema)

        self.assertFalse(comparison.matches)
        self.assertGreater(len(comparison.differences), 0)
        different_fields = {difference.field for difference in comparison.differences}
        self.assertIn("MFR URL", different_fields)
        self.assertIn("MANUFACTURER_NAME", different_fields)
        self.assertNotIn("Mfg_Part_Num", different_fields)
        self.assertNotIn("Part_Desc", different_fields)
        self.assertEqual(comparison.mfg_part_num, FIXTURE_PART)

    def test_empty_row_and_comparison_require_exact_schema_shape(self) -> None:
        schema = load_delivery_schema(DELIVERY_CSV)
        empty = schema.empty_row()

        self.assertEqual(list(empty), list(schema.columns))
        self.assertTrue(all(value == "" for value in empty.values()))


if __name__ == "__main__":
    unittest.main()
