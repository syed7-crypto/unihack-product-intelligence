import unittest

from src.product_intelligence.catalog_input import CatalogInputRow, INPUT_COLUMNS
from src.product_intelligence.catalogue_batch import run_catalogue_batch
from src.product_intelligence.catalogue_enrichment import CatalogueEnrichmentResult
from src.product_intelligence.delivery_schema import DeliverySchema
from src.product_intelligence.manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    RetrievedPayload,
)
from src.product_intelligence.reference_data import BrandReference, ManufacturerReference
from src.product_intelligence.review import ReviewReport
from src.product_intelligence.runtime_policy import (
    RuntimeAuthorityEvidence,
    resolve_identity_and_source_policy,
)
from src.product_intelligence.source_discovery import InMemorySourceSearchProvider, SearchResult


def row(mpn: str = "RUNTIME-1", manufacturer: str = "Unknown distributor") -> CatalogInputRow:
    return CatalogInputRow(
        Mfg_Part_Num=mpn,
        Part_Desc="runtime fixture",
        E1_Brand="-- Unbranded --",
        Unilog_Brand="-- No Unilog Brand --",
        DIB_Brand="-- No DIB Brand --",
        Part_Manuf=manufacturer,
    )


def schema() -> DeliverySchema:
    return DeliverySchema((*INPUT_COLUMNS, "MFR URL"))


class RuntimePolicyTests(unittest.TestCase):
    def provider(self, fetched: list[str], body: bytes = b"<h1>Model RUNTIME-1</h1>"):
        def fetcher(url: str, timeout: float) -> RetrievedPayload:
            fetched.append(url)
            return RetrievedPayload(200, {"content-type": "text/html"}, body)

        return ManufacturerEnrichmentProvider(fetcher=fetcher)

    def test_known_manufacturer_resolves_without_search(self) -> None:
        search = InMemorySourceSearchProvider({})
        result = resolve_identity_and_source_policy(
            row(manufacturer="Frigidaire"),
            search_provider=search,
            manufacturer_reference=ManufacturerReference(["Frigidaire"]),
        )
        self.assertEqual(result.state, "known")
        self.assertEqual(search.queries, [])
        self.assertEqual(result.approved_domains, ("www.frigidaire.com", "frigidaire.com", "frigidaire.bynder.com"))

    def test_known_brand_resolves_without_search(self) -> None:
        candidate = row()
        candidate.E1_Brand = "DEWALT"
        search = InMemorySourceSearchProvider({})
        result = resolve_identity_and_source_policy(
            candidate,
            search_provider=search,
            brand_reference=BrandReference(["DEWALT"]),
        )
        self.assertEqual(result.state, "known")
        self.assertEqual(result.resolved_identity, "DEWALT")
        self.assertEqual(search.queries, [])

    def test_manufacturer_policy_takes_precedence_over_brand(self) -> None:
        candidate = row(manufacturer="Frigidaire")
        candidate.E1_Brand = "DEWALT"
        result = resolve_identity_and_source_policy(
            candidate,
            manufacturer_reference=ManufacturerReference(["Frigidaire"]),
            brand_reference=BrandReference(["DEWALT"]),
        )
        self.assertEqual(result.state, "known")
        self.assertEqual(result.resolved_identity, "Frigidaire")

    def test_search_and_authority_verification_create_ephemeral_runtime_policy(self) -> None:
        url = "https://runtime.example/product/RUNTIME-1"
        search = InMemorySourceSearchProvider(
            {"RUNTIME-1": [SearchResult(url=url, title="Untrusted title", snippet="RUNTIME-1")]}
        )
        fetched: list[str] = []

        def authority(catalogue_row, candidate):
            if candidate.url == url:
                return RuntimeAuthorityEvidence(
                    controlled_identity="Runtime Manufacturer",
                    identity_kind="manufacturer",
                    domain="runtime.example",
                    reason="Controlled test authority attestation.",
                )
            return None

        result = resolve_identity_and_source_policy(
            row(), search_provider=search, enrichment_provider=self.provider(fetched),
            authority_verifier=authority,
        )
        self.assertEqual(result.state, "resolvable")
        self.assertEqual(result.resolved_identity, "Runtime Manufacturer")
        self.assertEqual(result.approved_domains, ("runtime.example",))
        self.assertEqual(fetched, [url])
        self.assertIsNotNone(result.runtime_policy)
        self.assertEqual(result.runtime_policy.governance_reason, "Controlled test authority attestation.")

    def test_runtime_policy_is_not_persisted(self) -> None:
        url = "https://runtime.example/product/RUNTIME-1"
        result = resolve_identity_and_source_policy(
            row(),
            search_provider=InMemorySourceSearchProvider({"RUNTIME-1": [SearchResult(url=url)]}),
            enrichment_provider=self.provider([]),
            authority_verifier=lambda _row, _candidate: RuntimeAuthorityEvidence(
                controlled_identity="Runtime Manufacturer",
                identity_kind="manufacturer",
                domain="runtime.example",
                reason="Test-only attestation.",
            ),
        )
        self.assertEqual(result.state, "resolvable")
        unresolved = resolve_identity_and_source_policy(row())
        self.assertEqual(unresolved.state, "unknown")

    def test_snippet_alone_cannot_create_runtime_policy(self) -> None:
        result = resolve_identity_and_source_policy(
            row(),
            search_provider=InMemorySourceSearchProvider(
                {"RUNTIME-1": [SearchResult(url="https://runtime.example/RUNTIME-1", snippet="Runtime Manufacturer")]}
            ),
            enrichment_provider=self.provider([]),
        )
        self.assertEqual(result.state, "unknown")

    def test_retailer_candidate_cannot_create_runtime_policy(self) -> None:
        fetched: list[str] = []
        result = resolve_identity_and_source_policy(
            row(),
            search_provider=InMemorySourceSearchProvider(
                {"RUNTIME-1": [SearchResult(url="https://retailer.example/RUNTIME-1")]}
            ),
            enrichment_provider=self.provider(fetched),
            authority_verifier=lambda _row, _candidate: None,
        )
        self.assertEqual(result.state, "unknown")
        self.assertEqual(fetched, [])

    def test_exact_mpn_verification_remains_mandatory(self) -> None:
        url = "https://runtime.example/product/RUNTIME-1"
        result = resolve_identity_and_source_policy(
            row(),
            search_provider=InMemorySourceSearchProvider({"RUNTIME-1": [SearchResult(url=url)]}),
            enrichment_provider=self.provider([], b"<h1>Model OTHER-1</h1>"),
            authority_verifier=lambda _row, _candidate: RuntimeAuthorityEvidence(
                controlled_identity="Runtime Manufacturer",
                identity_kind="manufacturer",
                domain="runtime.example",
                reason="Test-only attestation.",
            ),
        )
        self.assertEqual(result.state, "unknown")
        self.assertIsNone(result.runtime_policy)

    def test_unknown_runtime_resolution_is_needs_review_in_batch(self) -> None:
        result = run_catalogue_batch(
            [row()], schema(), discovery_enabled=True,
            runtime_policy_resolution_enabled=True,
            search_provider=InMemorySourceSearchProvider({"RUNTIME-1": []}),
        )
        self.assertEqual(result.needs_review_rows, 1)
        self.assertEqual(result.row_results[0].review.status, "needs_review")
        self.assertEqual(result.delivery_rows[0]["Mfg_Part_Num"], "RUNTIME-1")

    def test_resolvable_runtime_policy_reaches_batch_enricher(self) -> None:
        url = "https://runtime.example/product/RUNTIME-1"
        captured: list[object] = []

        def enricher(catalogue_row, _urls, delivery_schema, **kwargs):
            captured.extend(kwargs.get("verified_sources") or ())
            return CatalogueEnrichmentResult(
                catalogue_row=catalogue_row,
                pipeline_result=None,
                delivery_row=delivery_schema.empty_row(),
                review=ReviewReport(status="ready"),
            )

        result = run_catalogue_batch(
            [row()], schema(), discovery_enabled=True,
            runtime_policy_resolution_enabled=True,
            search_provider=InMemorySourceSearchProvider({"RUNTIME-1": [SearchResult(url=url)]}),
            provider=self.provider([]),
            runtime_authority_verifier=lambda _row, _candidate: RuntimeAuthorityEvidence(
                controlled_identity="Runtime Manufacturer",
                identity_kind="manufacturer",
                domain="runtime.example",
                reason="Test-only attestation.",
            ),
            row_enricher=enricher,
        )
        self.assertEqual(result.ready_rows, 1)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].url, url)


if __name__ == "__main__":
    unittest.main()
