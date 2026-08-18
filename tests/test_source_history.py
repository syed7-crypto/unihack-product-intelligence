import unittest

from src.product_intelligence.catalog_input import CatalogInputRow
from src.product_intelligence.manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    ManufacturerSource,
    RetrievedPayload,
)
from src.product_intelligence.source_discovery import (
    InMemorySourceSearchProvider,
    ManufacturerSourcePolicy,
    discover_and_verify_sources,
)
from src.product_intelligence.source_history import VerifiedSourceHistory


def row(mpn: str) -> CatalogInputRow:
    return CatalogInputRow(
        Mfg_Part_Num=mpn,
        Part_Desc="Hunter fan fixture",
        E1_Brand="-- Unbranded --",
        Unilog_Brand="-- No Unilog Brand --",
        DIB_Brand="Hunter",
        Part_Manuf="Hunter Fan Company",
    )


def policy() -> ManufacturerSourcePolicy:
    return ManufacturerSourcePolicy(
        manufacturer_name="Hunter",
        approved_domains=("hunterfan.com",),
        query_templates=("{part_number} {manufacturer}",),
    )


def verified_source(url: str, mpn: str) -> ManufacturerSource:
    return ManufacturerSource(
        url=url,
        source_type="web",
        manufacturer_domain="hunterfan.com",
        source_name="Hunter product page",
        content=f"<h1>{mpn}</h1>".encode(),
        exact_mpn_verified=True,
    )


class VerifiedSourceHistoryTests(unittest.TestCase):
    def test_approved_manufacturer_domain_can_be_reused(self) -> None:
        history = VerifiedSourceHistory()
        history.record_verified_source(
            "Hunter", "59210", verified_source("https://hunterfan.com/59210", "59210")
        )
        history.record_verified_source(
            "Hunter", "59211", verified_source("https://retailer.example/59211", "59211")
        )

        self.assertEqual(
            history.candidate_urls("hunter", policy()),
            ["https://hunterfan.com/59210"],
        )

    def test_previously_verified_source_is_available_as_a_future_candidate(self) -> None:
        history = VerifiedSourceHistory()
        url = "https://hunterfan.com/product"
        history.record_verified_source("Hunter", "59210", verified_source(url, "59210"))
        provider = ManufacturerEnrichmentProvider(
            fetcher=lambda _url, _timeout: RetrievedPayload(
                200, {"content-type": "text/html"}, b"<h1>59211</h1>"
            )
        )

        result = discover_and_verify_sources(
            row("59211"),
            policy(),
            InMemorySourceSearchProvider({}),
            provider,
            verified_source_history=history,
        )

        self.assertEqual(result.discovery.status, "found")
        self.assertEqual(result.discovery.candidates[0].url, url)
        self.assertEqual([source.url for source in result.verified_sources], [url])

    def test_current_exact_mpn_is_verified_for_reused_url(self) -> None:
        history = VerifiedSourceHistory()
        url = "https://hunterfan.com/product"
        history.record_verified_source("Hunter", "59210", verified_source(url, "59210"))
        provider = ManufacturerEnrichmentProvider(
            fetcher=lambda _url, _timeout: RetrievedPayload(
                200, {"content-type": "text/html"}, b"<h1>59210</h1>"
            )
        )

        result = discover_and_verify_sources(
            row("59210"), policy(), InMemorySourceSearchProvider({}), provider,
            verified_source_history=history,
        )

        self.assertEqual(len(result.verified_sources), 1)
        self.assertTrue(result.verified_sources[0].exact_mpn_verified)

    def test_source_verified_for_mpn_a_cannot_verify_mpn_b(self) -> None:
        history = VerifiedSourceHistory()
        url = "https://hunterfan.com/product"
        history.record_verified_source("Hunter", "59210", verified_source(url, "59210"))
        provider = ManufacturerEnrichmentProvider(
            fetcher=lambda _url, _timeout: RetrievedPayload(
                200, {"content-type": "text/html"}, b"<h1>59210</h1>"
            )
        )

        result = discover_and_verify_sources(
            row("59211"), policy(), InMemorySourceSearchProvider({}), provider,
            verified_source_history=history,
        )

        self.assertEqual(result.verified_sources, [])
        self.assertEqual(result.diagnostics[0].verification_status, "failed")
        self.assertIn("Exact MPN", result.diagnostics[0].error or "")


if __name__ == "__main__":
    unittest.main()
