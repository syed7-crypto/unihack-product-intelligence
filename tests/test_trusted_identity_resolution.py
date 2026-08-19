import unittest

from src.product_intelligence.catalog_input import CatalogInputRow
from src.product_intelligence.catalogue_enrichment import (
    _map_resolved_identity,
    _resolve_references,
)
from src.product_intelligence.review import build_review_report
from src.product_intelligence.runtime_policy import IdentityResolutionResult


class TrustedIdentityResolutionTests(unittest.TestCase):
    def row(self) -> CatalogInputRow:
        return CatalogInputRow(
            Mfg_Part_Num="GENERIC-1",
            Part_Desc="Generic product",
            E1_Brand="-- Unbranded --",
            Unilog_Brand="-- No Unilog Brand --",
            DIB_Brand="-- No DIB Brand --",
            Part_Manuf="Distributor Catalogue Co (0001)",
        )

    def trusted_brand_identity(self) -> IdentityResolutionResult:
        return IdentityResolutionResult(
            state="known",
            resolved_identity="Trusted Brand",
            identity_kind="brand",
            approved_domains=("manufacturer.example",),
            reason="Controlled source policy verified the brand identity.",
        )

    def test_trusted_brand_identity_does_not_review_raw_distributor_manufacturer(self) -> None:
        resolution = _resolve_references(self.row(), None, None, self.trusted_brand_identity())

        report = build_review_report(
            pipeline_result=None,
            source_diagnostics=[],
            reference_resolution=resolution,
            mapping_diagnostics=[],
            evaluation_comparison=None,
        )

        self.assertNotIn("MANUFACTURER_UNRESOLVED", {issue.code for issue in report.issues})
        self.assertEqual(resolution.manufacturer.status, "unresolved")
        self.assertEqual(resolution.runtime_identity.resolved_value, "Trusted Brand")

    def test_trusted_identity_maps_separately_without_overwriting_raw_field(self) -> None:
        resolution = _resolve_references(self.row(), None, None, self.trusted_brand_identity())
        delivery = {
            "Part_Manuf": self.row().Part_Manuf,
            "MANUFACTURER_NAME": "",
            "BRAND_NAME": "",
        }

        _map_resolved_identity(delivery, resolution)

        self.assertEqual(delivery["Part_Manuf"], "Distributor Catalogue Co (0001)")
        self.assertEqual(delivery["MANUFACTURER_NAME"], "")
        self.assertEqual(delivery["BRAND_NAME"], "Trusted Brand")

    def test_untrusted_unresolved_identity_still_requires_review(self) -> None:
        resolution = _resolve_references(self.row(), None, None, None)
        report = build_review_report(
            pipeline_result=None,
            source_diagnostics=[],
            reference_resolution=resolution,
            mapping_diagnostics=[],
            evaluation_comparison=None,
        )

        self.assertIn("MANUFACTURER_UNRESOLVED", {issue.code for issue in report.issues})
        self.assertEqual(report.status, "needs_review")


if __name__ == "__main__":
    unittest.main()
