import unittest

from src.product_intelligence.catalog_input import CatalogInputRow, INPUT_COLUMNS
from src.product_intelligence.catalogue_batch import run_catalogue_batch
from src.product_intelligence.catalogue_enrichment import CatalogueEnrichmentResult
from src.product_intelligence.delivery_schema import DeliverySchema
from src.product_intelligence.manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    RetrievedPayload,
)
from src.product_intelligence.review import ReviewIssue, ReviewReport
from src.product_intelligence.source_discovery import (
    InMemorySourceSearchProvider,
    ManufacturerSourcePolicy,
    SearchResult,
)


def schema() -> DeliverySchema:
    return DeliverySchema((*INPUT_COLUMNS, "MFR URL", "ATTRIBUTE_LABEL 1", "ATTRIBUTE_VALUE 1", "ATTRIBUTE_UOM 1"))


def row(mpn: str) -> CatalogInputRow:
    return CatalogInputRow(
        Mfg_Part_Num=mpn,
        Part_Desc=f"Description {mpn}",
        E1_Brand="-- Unbranded --",
        Unilog_Brand="-- No Unilog Brand --",
        DIB_Brand="-- No DIB Brand --",
        Part_Manuf="pilot manufacturer",
    )


def boundary_enricher(captured: dict[str, object]):
    """Test double for the existing row boundary, not a second pipeline."""
    def enrich(catalogue_row, _urls, delivery_schema, **kwargs):
        sources = list(kwargs.get("verified_sources") or ())
        diagnostics = list(kwargs.get("initial_source_diagnostics") or ())
        captured[catalogue_row.Mfg_Part_Num] = {
            "sources": sources,
            "diagnostics": diagnostics,
            "runtime_identity": kwargs.get("runtime_identity"),
        }
        delivery = delivery_schema.empty_row()
        if sources:
            delivery["MFR URL"] = sources[0].url
            delivery["ATTRIBUTE_VALUE 1"] = "must not be sourced from search metadata"
            review = ReviewReport(status="ready")
        else:
            review = ReviewReport(
                status="blocked",
                issues=[
                    ReviewIssue(
                        code="SOURCE_RETRIEVAL_FAILED",
                        severity="blocking",
                        scope="row",
                        message="No verified source was available.",
                    )
                ],
            )
        return CatalogueEnrichmentResult(
            catalogue_row=catalogue_row,
            pipeline_result=None,
            delivery_row=delivery,
            review=review,
        )
    return enrich


class CatalogueBatchDiscoveryTests(unittest.TestCase):
    def provider(self, fetched_urls: list[str], body: bytes = b"<h1>Hunter model 59210</h1>"):
        def fetcher(url: str, timeout: float) -> RetrievedPayload:
            fetched_urls.append(url)
            return RetrievedPayload(200, {"content-type": "text/html"}, body)

        return ManufacturerEnrichmentProvider(fetcher=fetcher)

    def test_verified_discovered_source_reaches_enrichment(self) -> None:
        official = "https://www.hunterfan.com/59210"
        search = InMemorySourceSearchProvider(
            {query: [SearchResult(url=official, title="Hunter 59210", snippet="59210")] for query in (
                "59210", "59210 Hunter"
            )}
        )
        fetched: list[str] = []
        captured: dict[str, object] = {}
        result = run_catalogue_batch(
            [row("59210")],
            schema(),
            discovery_enabled=True,
            search_provider=search,
            provider=self.provider(fetched),
            row_enricher=boundary_enricher(captured),
        )

        self.assertEqual(result.ready_rows, 1)
        self.assertEqual(fetched, [official])
        self.assertEqual([source.url for source in captured["59210"]["sources"]], [official])

    def test_controlled_policy_identity_is_handed_to_row_enrichment(self) -> None:
        official = "https://manufacturer.example/products/GENERIC-1"
        search = InMemorySourceSearchProvider(
            {
                "GENERIC-1": [SearchResult(url=official)],
                "GENERIC-1 Example Brand": [SearchResult(url=official)],
            }
        )
        captured: dict[str, object] = {}
        policy = ManufacturerSourcePolicy(
            manufacturer_name="Example Brand",
            identity_kind="brand",
            approved_domains=("manufacturer.example",),
        )

        result = run_catalogue_batch(
            [row("GENERIC-1")],
            schema(),
            discovery_enabled=True,
            discovery_policy_resolver=lambda _row: policy,
            search_provider=search,
            provider=self.provider([], b"<h1>Example Brand GENERIC-1</h1>"),
            row_enricher=boundary_enricher(captured),
        )

        self.assertEqual(result.ready_rows, 1)
        identity = captured["GENERIC-1"]["runtime_identity"]
        self.assertEqual(identity.resolved_identity, "Example Brand")
        self.assertEqual(identity.identity_kind, "brand")
        self.assertEqual(len(captured["GENERIC-1"]["sources"]), 1)

    def test_unapproved_and_retailer_results_never_reach_retrieval_or_evidence(self) -> None:
        official = "https://www.hunterfan.com/59210"
        retailer = "https://retailer.example/59210"
        search = InMemorySourceSearchProvider(
            {query: [
                SearchResult(url=retailer, title="Retailer 59210", snippet="59210"),
                SearchResult(url=official, title="Hunter 59210", snippet="59210"),
            ] for query in ("59210", "59210 Hunter")}
        )
        fetched: list[str] = []
        captured: dict[str, object] = {}
        run_catalogue_batch(
            [row("59210")], schema(), discovery_enabled=True,
            search_provider=search, provider=self.provider(fetched),
            row_enricher=boundary_enricher(captured),
        )

        self.assertEqual(fetched, [official])
        sources = captured["59210"]["sources"]
        self.assertEqual([source.url for source in sources], [official])
        self.assertNotIn("Retailer 59210", str(sources))

    def test_exact_mpn_mismatch_remains_blocked(self) -> None:
        url = "https://www.hunterfan.com/59210"
        search = InMemorySourceSearchProvider(
            {query: [SearchResult(url=url, title="Hunter 59210", snippet="59210")] for query in (
                "59210", "59210 Hunter"
            )}
        )
        result = run_catalogue_batch(
            [row("59210")], schema(), discovery_enabled=True,
            search_provider=search,
            provider=self.provider([], b"<h1>Hunter model 59211</h1>"),
        )

        self.assertEqual(result.blocked_rows, 1)
        self.assertEqual(result.row_results[0].catalogue_row.Mfg_Part_Num, "59210")
        self.assertTrue(all(value == "" for key, value in result.delivery_rows[0].items()
                            if key.startswith("ATTRIBUTE_")))

    def test_search_failure_isolated_to_one_row(self) -> None:
        official = "https://www.dewalt.com/dwst41092"
        search = InMemorySourceSearchProvider(
            {query: [SearchResult(url=official, title="DEWALT DWST41092", snippet="DWST41092")]
             for query in ("DWST41092", "DWST41092 DEWALT")}
        )
        captured: dict[str, object] = {}

        class FailingSearch:
            def search(self, query, max_results):
                if query == "59210":
                    raise RuntimeError("controlled search failure")
                return search.search(query, max_results)

        result = run_catalogue_batch(
            [row("59210"), row("DWST41092")], schema(), discovery_enabled=True,
            search_provider=FailingSearch(),
            provider=self.provider([], b"<h1>DEWALT model DWST41092</h1>"),
            row_enricher=boundary_enricher(captured),
        )

        self.assertEqual(result.processed_rows, 2)
        self.assertEqual(result.row_results[0].review.status, "blocked")
        self.assertEqual(result.row_results[1].review.status, "ready")

    def test_explicit_urls_still_bypass_discovery(self) -> None:
        url = "https://www.frigidaire.com/explicit"
        fetched: list[str] = []
        captured: dict[str, object] = {}
        search = InMemorySourceSearchProvider({})
        result = run_catalogue_batch(
            [row("PDSH4816AF")], schema(),
            source_urls={"PDSH4816AF": [url]}, discovery_enabled=True,
            search_provider=search, provider=self.provider(fetched),
            row_enricher=boundary_enricher(captured),
        )

        self.assertEqual(result.processed_rows, 1)
        self.assertEqual(search.queries, [])
        self.assertEqual(captured["PDSH4816AF"]["sources"], [])

    def test_discovery_disabled_preserves_missing_source_behavior(self) -> None:
        search = InMemorySourceSearchProvider({"59210": [SearchResult(url="https://www.hunterfan.com/59210")]})
        result = run_catalogue_batch(
            [row("59210")], schema(), discovery_enabled=False,
            search_provider=search,
        )

        self.assertEqual(result.failed_rows, 1)
        self.assertEqual(search.queries, [])
        self.assertEqual(result.delivery_rows[0]["Mfg_Part_Num"], "59210")


if __name__ == "__main__":
    unittest.main()
