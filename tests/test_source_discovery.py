import unittest
from unittest.mock import Mock, patch

from src.product_intelligence.catalog_input import CatalogInputRow
from src.product_intelligence.manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    RetrievedPayload,
)
from src.product_intelligence.reference_data import ReferenceResolutionResult
from src.product_intelligence.source_discovery import (
    InMemorySourceSearchProvider,
    ManufacturerSourcePolicy,
    SearchResult,
    discover_and_verify_sources,
    discover_manufacturer_sources,
    generate_discovery_queries,
)


MPN = "59210"


def catalogue_row() -> CatalogInputRow:
    return CatalogInputRow(
        Mfg_Part_Num=MPN,
        Part_Desc='52" BZ Sent Hunter Fan',
        E1_Brand="-- Unbranded --",
        Unilog_Brand="-- No Unilog Brand --",
        DIB_Brand="Hunter",
        Part_Manuf="Hunter Fan Co (4381)",
    )


def resolved(reference_type: str, value: str) -> ReferenceResolutionResult:
    return ReferenceResolutionResult(
        input_value=value,
        resolved_value=value,
        status="resolved",
        reference_type=reference_type,
        reason="test reference",
    )


class FailingSearchProvider:
    def search(self, query: str, max_results: int) -> list[SearchResult]:
        raise RuntimeError("search unavailable")


class SourceDiscoveryTests(unittest.TestCase):
    def policy(self, *domains: str) -> ManufacturerSourcePolicy:
        return ManufacturerSourcePolicy(
            manufacturer_name="Hunter Fan Company",
            approved_domains=domains or ("hunterfan.com", "image.hunterfan.com"),
            query_templates=("{part_number} {manufacturer}",),
        )

    def test_query_generation_is_deterministic_and_uses_resolved_identity(self) -> None:
        queries = generate_discovery_queries(
            catalogue_row(),
            self.policy(),
            manufacturer_reference=resolved("manufacturer", "Hunter Fan Company"),
            brand_reference=resolved("brand", "Hunter"),
        )

        self.assertEqual(queries, ["59210 Hunter Fan Company"])

    def test_discovery_filters_https_domain_and_source_kind_without_verifying(self) -> None:
        query = "59210 Hunter Fan Company"
        provider = InMemorySourceSearchProvider(
            {
                query: [
                    SearchResult(url="https://hunterfan.com/product/59210", title="59210"),
                    SearchResult(url="http://hunterfan.com/old/59210"),
                    SearchResult(url="https://amazon.com/59210", snippet="59210"),
                    SearchResult(url="https://image.hunterfan.com/manual.pdf"),
                ]
            }
        )

        result = discover_manufacturer_sources(catalogue_row(), self.policy(), provider)

        self.assertEqual(result.status, "found")
        self.assertEqual([item.status for item in result.candidates], ["candidate", "rejected", "rejected", "candidate"])
        self.assertEqual([item.source_kind for item in result.candidates], ["webpage", "webpage", "webpage", "pdf"])
        self.assertTrue(result.candidates[2].exact_mpn_in_result)
        self.assertEqual(result.candidates[2].status, "rejected")

    def test_duplicate_urls_are_deduplicated_in_first_seen_order(self) -> None:
        provider = InMemorySourceSearchProvider(
            {
                "59210 Hunter Fan Company": [
                    SearchResult(url="https://hunterfan.com/a"),
                    SearchResult(url="https://hunterfan.com/a"),
                    SearchResult(url="https://hunterfan.com/b"),
                ]
            }
        )

        result = discover_manufacturer_sources(catalogue_row(), self.policy(), provider)

        self.assertEqual([item.url for item in result.candidates], ["https://hunterfan.com/a", "https://hunterfan.com/b"])

    def test_empty_and_failed_searches_are_explicit(self) -> None:
        empty = discover_manufacturer_sources(
            catalogue_row(),
            self.policy(),
            InMemorySourceSearchProvider({}),
        )
        failed = discover_manufacturer_sources(catalogue_row(), self.policy(), FailingSearchProvider())

        self.assertEqual(empty.status, "no_candidates")
        self.assertEqual(failed.status, "failed")
        self.assertTrue(failed.errors)

    def test_unresolved_manufacturer_does_not_invent_a_domain(self) -> None:
        policy = ManufacturerSourcePolicy(
            manufacturer_name=None,
            approved_domains=(),
            query_templates=("{part_number}",),
        )
        provider = InMemorySourceSearchProvider(
            {"59210": [SearchResult(url="https://hunterfan.com/59210")]}
        )

        result = discover_manufacturer_sources(catalogue_row(), policy, provider)

        self.assertEqual(result.candidates[0].status, "rejected")
        self.assertEqual(policy.approved_domains, ())

    def test_unresolved_manufacturer_is_not_passed_to_verification(self) -> None:
        provider = InMemorySourceSearchProvider(
            {"59210": [SearchResult(url="https://hunterfan.com/59210", snippet="59210")]}
        )
        enrichment = ManufacturerEnrichmentProvider(
            approved_domains={"hunterfan.com"},
            fetcher=lambda url, timeout: RetrievedPayload(200, {"content-type": "text/html"}, b"59210"),
        )
        unresolved = ReferenceResolutionResult(
            input_value="Unknown",
            resolved_value=None,
            status="unresolved",
            reference_type="manufacturer",
            reason="No controlled match",
        )

        result = discover_and_verify_sources(
            catalogue_row(),
            ManufacturerSourcePolicy(
                approved_domains=("hunterfan.com",),
                query_templates=("{part_number}",),
            ),
            provider,
            enrichment,
            manufacturer_reference=unresolved,
        )

        self.assertEqual(result.verified_sources, [])
        self.assertEqual(result.diagnostics[0].verification_status, "rejected")
        self.assertIn("unresolved", result.diagnostics[0].error or "")

    def test_discovery_does_not_modify_provider_allowlist_or_create_pipeline_objects(self) -> None:
        search = InMemorySourceSearchProvider(
            {"59210 Hunter Fan Company": [SearchResult(url="https://hunterfan.com/59210")]}
        )
        enrichment = ManufacturerEnrichmentProvider(
            approved_domains={"hunterfan.com"},
            fetcher=lambda url, timeout: RetrievedPayload(200, {"content-type": "text/html"}, b"59210"),
        )
        before = enrichment.approved_domains

        result = discover_manufacturer_sources(catalogue_row(), self.policy(), search)

        self.assertEqual(enrichment.approved_domains, before)
        self.assertFalse(hasattr(result.candidates[0], "extracted_text"))
        self.assertNotIn("AttributeEvidence", result.model_dump_json())

    def test_snippet_mpn_is_not_verification_and_similar_mpn_fails(self) -> None:
        official_url = "https://hunterfan.com/59210"
        similar_url = "https://hunterfan.com/59210-spec"
        search = InMemorySourceSearchProvider(
            {
                "59210 Hunter Fan Company": [
                    SearchResult(url=official_url, title="Fan", snippet="Model 59210"),
                    SearchResult(url=similar_url, title="59210BF", snippet="Similar model"),
                ]
            }
        )
        fetcher = lambda url, timeout: RetrievedPayload(
            200,
            {"content-type": "text/html"},
            b"PDSH4816BF" if url == similar_url else b"No matching model in retrieved page",
        )
        enrichment = ManufacturerEnrichmentProvider(
            approved_domains={"hunterfan.com"}, fetcher=fetcher
        )

        result = discover_and_verify_sources(
            catalogue_row(), self.policy(), search, enrichment
        )

        self.assertTrue(result.discovery.candidates[0].exact_mpn_in_result)
        self.assertEqual(result.verified_sources, [])
        self.assertTrue(all(item.verification_status == "failed" for item in result.diagnostics))

    def test_exact_mpn_in_retrieved_official_content_is_the_only_verified_result(self) -> None:
        official_url = "https://hunterfan.com/59210"
        retailer_url = "https://homedepot.com/59210"
        search = InMemorySourceSearchProvider(
            {
                "59210 Hunter Fan Company": [
                    SearchResult(url=official_url, title="59210", snippet="Model 59210"),
                    SearchResult(url=retailer_url, title="59210", snippet="Model 59210"),
                ]
            }
        )
        fetcher = Mock(
            return_value=RetrievedPayload(
                200,
                {"content-type": "text/html"},
                b"<h1>Hunter model 59210</h1>",
            )
        )
        enrichment = ManufacturerEnrichmentProvider(
            approved_domains={"hunterfan.com"}, fetcher=fetcher
        )

        result = discover_and_verify_sources(
            catalogue_row(), self.policy(), search, enrichment
        )

        self.assertEqual([source.url for source in result.verified_sources], [official_url])
        self.assertEqual(fetcher.call_count, 1)
        self.assertEqual(result.diagnostics[1].verification_status, "rejected")

    def test_discovery_never_calls_gemini(self) -> None:
        provider = InMemorySourceSearchProvider(
            {"59210 Hunter Fan Company": [SearchResult(url="https://hunterfan.com/59210")]}
        )
        with patch("src.product_intelligence.gemini_client.create_gemini_client") as client:
            discover_manufacturer_sources(catalogue_row(), self.policy(), provider)
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
