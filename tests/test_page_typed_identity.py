import unittest

from src.product_intelligence.catalog_input import CatalogInputRow
from src.product_intelligence.catalogue_enrichment import _resolve_references
from src.product_intelligence.manufacturer_enrichment import ManufacturerEnrichmentProvider, RetrievedPayload
from src.product_intelligence.reference_data import ManufacturerReference
from src.product_intelligence.runtime_policy import (
    RuntimeDomainCandidate,
    resolve_identity_and_source_policy,
)
from src.product_intelligence.source_discovery import InMemorySourceSearchProvider


class PageTypedIdentityTests(unittest.TestCase):
    def test_structured_page_brand_becomes_brand_assertion(self) -> None:
        url = "https://diablo.example/products/MPN-1"
        body = b"""
        <title>MPN-1 Detail File Sanding Belt - Diablo Tools</title>
        <meta name="description" content="Diablo product MPN-1 sanding belt">
        <p>Freud product platform</p>
        <script>
          window.productState = {"item_num":"MPN-1", "brand":"Diablo",
          "product_title":"Diablo Detail File Sanding Belt"};
        </script>
        """

        result = resolve_identity_and_source_policy(
            CatalogInputRow(
                Mfg_Part_Num="MPN-1",
                Part_Desc="Diablo sanding belt",
                E1_Brand="-- Unbranded --",
                Unilog_Brand="-- No Unilog Brand --",
                DIB_Brand="-- No DIB Brand --",
                Part_Manuf="Freud",
            ),
            search_provider=InMemorySourceSearchProvider({}),
            enrichment_provider=ManufacturerEnrichmentProvider(
                fetcher=lambda _url, _timeout: RetrievedPayload(
                    200, {"content-type": "text/html"}, body
                )
            ),
            candidate_domain_provider=lambda _row: [
                RuntimeDomainCandidate(domain="diablo.example", discovery_url=url)
            ],
        )

        self.assertEqual(result.state, "resolvable")
        self.assertEqual(result.resolved_identity, "Diablo")
        self.assertEqual(result.identity_kind, "brand")
        self.assertIsNotNone(result.identity_assertion)
        self.assertEqual(result.identity_assertion.value, "Diablo")
        self.assertEqual(result.identity_assertion.kind, "brand")
        self.assertEqual(result.identity_assertion.source, "page_evidence")

        resolution = _resolve_references(
            CatalogInputRow(
                Mfg_Part_Num="MPN-1",
                Part_Desc="Diablo sanding belt",
                E1_Brand="-- Unbranded --",
                Unilog_Brand="-- No Unilog Brand --",
                DIB_Brand="-- No DIB Brand --",
                Part_Manuf="Freud",
            ),
            ManufacturerReference(["Freud"]),
            None,
            result,
        )
        self.assertEqual(resolution.manufacturer.resolved_value, "Freud")
        self.assertEqual(resolution.runtime_identity.resolved_value, "Diablo")
        self.assertEqual(resolution.runtime_identity.reference_type, "brand")


if __name__ == "__main__":
    unittest.main()
