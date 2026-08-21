import unittest

from src.product_intelligence.catalog_input import CatalogInputRow
from src.product_intelligence.catalogue_enrichment import _resolve_references
from src.product_intelligence.reference_data import (
    BrandManufacturerReference,
    BrandManufacturerRelationship,
    BrandReference,
    ManufacturerReference,
)
from src.product_intelligence.runtime_policy import (
    IdentityResolutionResult,
    _catalogue_identity_conflict,
)


def catalogue_row(
    *,
    manufacturer: str,
    brand: str = "-- No DIB Brand --",
) -> CatalogInputRow:
    return CatalogInputRow(
        Mfg_Part_Num="IDENTITY-1",
        Part_Desc="identity fixture",
        E1_Brand="-- Unbranded --",
        Unilog_Brand="-- No Unilog Brand --",
        DIB_Brand=brand,
        Part_Manuf=manufacturer,
    )


class TypedIdentityTests(unittest.TestCase):
    def test_freud_and_diablo_remain_separate_roles(self) -> None:
        row = catalogue_row(manufacturer="Freud")
        runtime = IdentityResolutionResult(
            state="resolvable",
            resolved_identity="Diablo",
            identity_kind="brand",
            reason="Verified page brand.",
        )
        result = _resolve_references(
            row,
            ManufacturerReference(["Freud"]),
            None,
            runtime,
        )

        self.assertEqual(result.manufacturer.resolved_value, "Freud")
        self.assertEqual(result.runtime_identity.resolved_value, "Diablo")
        self.assertEqual(result.runtime_identity.reference_type, "brand")
        self.assertEqual(result.manufacturer_assertion.kind, "manufacturer")
        self.assertEqual(result.brand_assertions["runtime_identity"].value, "Diablo")
        self.assertEqual(
            {(item.value, item.kind) for item in result.identity_assertions},
            {("Freud", "manufacturer"), ("Diablo", "brand")},
        )

    def test_festool_and_festo_remain_a_manufacturer_conflict(self) -> None:
        row = catalogue_row(manufacturer="Festool USA (FESTO)")
        conflict = _catalogue_identity_conflict(row, "Festo")
        self.assertIsNotNone(conflict)
        self.assertIn("conflicts", conflict)

    def test_parksite_and_timbertech_do_not_convert_distributor_to_manufacturer(self) -> None:
        row = catalogue_row(manufacturer="Parksite (6151)", brand="TIMBERTECH")
        result = _resolve_references(row, None, BrandReference(["TimberTech"]))

        self.assertIsNone(result.manufacturer.resolved_value)
        self.assertEqual(result.brands["DIB_Brand"].resolved_value, "TimberTech")
        self.assertEqual(result.brand_assertions["DIB_Brand"].kind, "brand")
        self.assertNotEqual(result.manufacturer_assertion, result.brand_assertions["DIB_Brand"])

    def test_controlled_relationship_is_optional_and_not_created_from_runtime_brand(self) -> None:
        row = catalogue_row(manufacturer="Freud Inc (2435)")
        runtime = IdentityResolutionResult(
            state="resolvable",
            resolved_identity="Diablo",
            identity_kind="brand",
            reason="Verified page brand.",
        )
        without_relationship = _resolve_references(row, None, None, runtime)
        self.assertIsNone(without_relationship.manufacturer.resolved_value)

        relationship = BrandManufacturerReference(
            [
                BrandManufacturerRelationship(
                    brand="Approved Brand",
                    manufacturer="Approved Manufacturer",
                    reason="Controlled test relationship.",
                )
            ]
        )
        with_unrelated_relationship = _resolve_references(
            row, None, None, runtime, relationship
        )
        self.assertIsNone(with_unrelated_relationship.manufacturer.resolved_value)

    def test_only_explicit_controlled_relationship_can_resolve_manufacturer(self) -> None:
        row = catalogue_row(manufacturer="Catalogue Organization", brand="Approved Brand")
        relationship = BrandManufacturerReference(
            [
                BrandManufacturerRelationship(
                    brand="Approved Brand",
                    manufacturer="Approved Manufacturer",
                    reason="Controlled test relationship.",
                )
            ]
        )
        result = _resolve_references(row, None, BrandReference(["Approved Brand"]), None, relationship)

        self.assertEqual(result.manufacturer.resolved_value, "Approved Manufacturer")
        self.assertEqual(result.manufacturer_assertion.source, "controlled_reference")

    def test_raw_catalogue_fields_are_preserved(self) -> None:
        row = catalogue_row(manufacturer="Freud Inc (2435)", brand="TIMBERTECH")
        raw = row.raw_fields()
        _resolve_references(row, None, BrandReference(["TIMBERTECH"]))
        self.assertEqual(row.Part_Manuf, "Freud Inc (2435)")
        self.assertEqual(row.DIB_Brand, "TIMBERTECH")
        self.assertEqual(raw["Part_Manuf"], "Freud Inc (2435)")
        self.assertEqual(raw["DIB_Brand"], "TIMBERTECH")


if __name__ == "__main__":
    unittest.main()
