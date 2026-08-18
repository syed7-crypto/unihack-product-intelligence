"""Tests for deterministic, test-only controlled reference data."""

import unittest
from pathlib import Path

from src.product_intelligence.catalog_input import load_catalog_rows, select_catalog_row
from src.product_intelligence.delivery_output import map_raw_fields_to_delivery
from src.product_intelligence.delivery_schema import load_delivery_schema
from src.product_intelligence.reference_data import (
    AttributeReference,
    AttributeRule,
    BrandReference,
    ManufacturerReference,
    TaxonomyPath,
    TaxonomyReference,
    UOMReference,
    resolve_catalog_row_references,
)


MOCK_MANUFACTURERS = ManufacturerReference(["Rheem Manufacturing", "Whirlpool Corporation"])
MOCK_BRANDS = BrandReference(["FRIGIDAIRE®", "Whirlpool®", "3M"])
MOCK_TAXONOMY = TaxonomyReference(
    [
        TaxonomyPath(
            dept="Appliances",
            class_name="Large Appliances",
            fine="Dishwashers",
            classpath="Appliances & Consumer Electronics>Kitchen Appliances>Built-In Dishwashers",
        )
    ]
)
MOCK_ATTRIBUTES = AttributeReference(
    {
        "Dishwasher": {
            "Material": AttributeRule(label="Material", allowed_values=("Stainless Steel", "Plastic")),
            "Voltage Rating": AttributeRule(label="Voltage Rating", allowed_values=("120", "240"), allowed_uoms=("V",)),
        }
    }
)
MOCK_UOMS = UOMReference({"V": ("v", "volt", "volts"), "A": ("a", "amp", "amps")})

DATA_DIRECTORY = Path(r"C:\Users\syed7\Downloads")
INPUT_CSV = DATA_DIRECTORY / "Unihack_ Sample Dataset - Input.csv"
DELIVERY_CSV = DATA_DIRECTORY / "Unihack_ Expected Output - Delivery Format.csv"


class ReferenceDataTests(unittest.TestCase):
    def test_exact_manufacturer_resolution(self) -> None:
        result = MOCK_MANUFACTURERS.resolve("Rheem Manufacturing")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.resolved_value, "Rheem Manufacturing")
        self.assertEqual(result.reference_type, "manufacturer")

    def test_normalized_manufacturer_resolution(self) -> None:
        result = MOCK_MANUFACTURERS.resolve("  rheem   manufacturing ")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.resolved_value, "Rheem Manufacturing")

    def test_unresolved_manufacturer_does_not_fallback(self) -> None:
        result = MOCK_MANUFACTURERS.resolve("Unknown Manufacturer")

        self.assertEqual(result.status, "unresolved")
        self.assertIsNone(result.resolved_value)

    def test_exact_brand_resolution(self) -> None:
        result = MOCK_BRANDS.resolve("FRIGIDAIRE®")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.resolved_value, "FRIGIDAIRE®")

    def test_placeholder_brand_is_unresolved(self) -> None:
        result = MOCK_BRANDS.resolve("-- Unbranded --")

        self.assertEqual(result.status, "unresolved")
        self.assertIsNone(result.resolved_value)
        self.assertIn("placeholder", result.reason)

    def test_unresolved_brand_does_not_fallback(self) -> None:
        result = MOCK_BRANDS.resolve("Unknown Brand")

        self.assertEqual(result.status, "unresolved")
        self.assertIsNone(result.resolved_value)

    def test_approved_taxonomy_value(self) -> None:
        result = MOCK_TAXONOMY.validate(
            "Appliances",
            "Large Appliances",
            "Dishwashers",
            "Appliances & Consumer Electronics>Kitchen Appliances>Built-In Dishwashers",
        )

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.reference_type, "taxonomy")

    def test_invalid_taxonomy_value(self) -> None:
        result = MOCK_TAXONOMY.validate(
            "Appliances",
            "Large Appliances",
            "Washers",
            "Appliances & Consumer Electronics>Kitchen Appliances>Washers",
        )

        self.assertEqual(result.status, "invalid")
        self.assertIsNone(result.resolved_value)

    def test_approved_attribute_value(self) -> None:
        result = MOCK_ATTRIBUTES.validate_value("Dishwasher", "Material", "stainless steel")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.resolved_value, "Stainless Steel")
        self.assertEqual(MOCK_ATTRIBUTES.allowed_values("Dishwasher", "Material"), ("Stainless Steel", "Plastic"))

    def test_invalid_attribute_value(self) -> None:
        result = MOCK_ATTRIBUTES.validate_value("Dishwasher", "Material", "Carbon Steel")

        self.assertEqual(result.status, "invalid")
        self.assertIsNone(result.resolved_value)

    def test_numeric_or_free_form_attribute_without_lov_is_not_unresolved(self) -> None:
        attributes = AttributeReference(
            {"Valve": {"Length": AttributeRule(label="Length", value_kind="numeric")}}
        )

        result = attributes.validate_value("Valve", "Length", "25.4")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.resolved_value, "25.4")

    def test_explicit_lov_without_values_remains_unresolved(self) -> None:
        attributes = AttributeReference(
            {"Valve": {"Finish": AttributeRule(label="Finish", value_kind="lov")}}
        )

        result = attributes.validate_value("Valve", "Finish", "Polished")

        self.assertEqual(result.status, "unresolved")

    def test_approved_uom_and_explicit_alias(self) -> None:
        result = MOCK_UOMS.resolve("volt")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.resolved_value, "V")

    def test_invalid_uom_does_not_invent_abbreviation(self) -> None:
        result = MOCK_UOMS.resolve("volts per minute")

        self.assertEqual(result.status, "invalid")
        self.assertIsNone(result.resolved_value)

    def test_missing_attribute_reference_is_unresolved(self) -> None:
        result = MOCK_ATTRIBUTES.attributes_for("Unknown Product")

        self.assertEqual(result.status, "unresolved")
        self.assertIsNone(result.resolved_value)

    def test_catalog_row_resolution_does_not_copy_raw_manufacturer_or_placeholder_brands(self) -> None:
        if not INPUT_CSV.exists() or not DELIVERY_CSV.exists():
            self.skipTest("The supplied UniHack CSV files are not available.")
        row = select_catalog_row(load_catalog_rows(INPUT_CSV), "PDSH4816AF")
        schema = load_delivery_schema(DELIVERY_CSV)

        resolution = resolve_catalog_row_references(row, MOCK_MANUFACTURERS, MOCK_BRANDS)
        delivery_row = map_raw_fields_to_delivery(row, schema)

        self.assertEqual(resolution.manufacturer.status, "unresolved")
        self.assertIsNone(resolution.manufacturer.resolved_value)
        self.assertEqual(resolution.brands["E1_Brand"].status, "unresolved")
        self.assertEqual(resolution.brands["E1_Brand"].input_value, "-- Unbranded --")
        self.assertEqual(delivery_row["MANUFACTURER_NAME"], "")
        self.assertEqual(delivery_row["BRAND_NAME"], "")
        self.assertEqual(delivery_row["Part_Manuf"], row.Part_Manuf)


if __name__ == "__main__":
    unittest.main()
