import unittest

from src.product_intelligence.catalog_input import CatalogInputRow
from src.product_intelligence.manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    RetrievedPayload,
)
from src.product_intelligence.runtime_policy import (
    RuntimeDomainCandidate,
    resolve_identity_and_source_policy,
)
from src.product_intelligence.source_discovery import InMemorySourceSearchProvider, SearchResult


def row() -> CatalogInputRow:
    return CatalogInputRow(
        Mfg_Part_Num="MPN-1",
        Part_Desc="Acme Widget",
        E1_Brand="-- Unbranded --",
        Unilog_Brand="-- No Unilog Brand --",
        DIB_Brand="-- No DIB Brand --",
        Part_Manuf="Acme",
    )


def runtime(candidate: RuntimeDomainCandidate, fetcher, *, search=None, max_attempts=3):
    provider = ManufacturerEnrichmentProvider(
        approved_domains={candidate.domain},
        fetcher=fetcher,
    )
    return resolve_identity_and_source_policy(
        row(),
        search_provider=search or InMemorySourceSearchProvider({}),
        enrichment_provider=provider,
        candidate_domain_provider=lambda _row: [candidate],
        max_source_attempts=max_attempts,
    )


class CandidateTelemetryTests(unittest.TestCase):
    def test_successful_candidate_telemetry(self) -> None:
        url = "https://approved.example/product/mpn-1"

        def fetch(_url, _timeout):
            return RetrievedPayload(
                200,
                {"content-type": "text/html"},
                b"<title>Acme Widget MPN-1</title><p>Acme Widget MPN-1</p>",
            )

        result = runtime(RuntimeDomainCandidate(domain="approved.example", discovery_url=url), fetch)
        item = result.candidate_telemetry[0]
        self.assertEqual(result.state, "resolvable")
        self.assertTrue(item.fetched)
        self.assertEqual(item.http_status, 200)
        self.assertEqual(item.content_type, "text/html")
        self.assertTrue(item.exact_mpn_verified)
        self.assertEqual(item.identity_value, "Acme")
        self.assertEqual(item.identity_kind, "manufacturer")
        self.assertEqual(item.identity_result, "verified")
        self.assertIsNone(item.rejection_code)

    def test_exact_mpn_failure_telemetry(self) -> None:
        url = "https://approved.example/product/no-mpn"

        def fetch(_url, _timeout):
            return RetrievedPayload(200, {"content-type": "text/html"}, b"<title>Acme Widget</title>")

        result = runtime(RuntimeDomainCandidate(domain="approved.example", discovery_url=url), fetch)
        item = result.candidate_telemetry[0]
        self.assertFalse(item.exact_mpn_verified)
        self.assertEqual(item.rejection_code, "EXACT_MPN_MISMATCH")
        self.assertEqual(item.http_status, 200)

    def test_identity_failure_telemetry(self) -> None:
        url = "https://approved.example/product/mpn-1"

        def fetch(_url, _timeout):
            return RetrievedPayload(
                200,
                {"content-type": "text/html"},
                b"<title>Other Widget MPN-1</title><p>Other Widget MPN-1</p>",
            )

        result = runtime(RuntimeDomainCandidate(domain="approved.example", discovery_url=url), fetch)
        item = result.candidate_telemetry[0]
        self.assertEqual(item.rejection_code, "IDENTITY_NOT_VERIFIED")
        self.assertEqual(item.identity_result, "not_verified")

    def test_governance_rejection_telemetry(self) -> None:
        result = runtime(
            RuntimeDomainCandidate(
                domain="homedepot.com",
                discovery_url="https://homedepot.com/p/mpn-1",
            ),
            lambda _url, _timeout: (_ for _ in ()).throw(AssertionError("must not fetch")),
        )
        item = result.candidate_telemetry[0]
        self.assertFalse(item.fetched)
        self.assertEqual(item.rejection_code, "RETAILER_DOMAIN_REJECTED")

    def test_attempt_limit_telemetry_preserves_skipped_candidate(self) -> None:
        first = "https://approved.example/product/first"
        second = "https://approved.example/product/second"
        search = InMemorySourceSearchProvider(
            {
                'site:approved.example "MPN-1"': [
                    SearchResult(url=first, title="Acme", snippet=""),
                    SearchResult(url=second, title="Acme", snippet=""),
                ]
            }
        )

        def fetch(_url, _timeout):
            return RetrievedPayload(200, {"content-type": "text/html"}, b"<title>Acme</title>")

        result = runtime(
            RuntimeDomainCandidate(domain="approved.example"),
            fetch,
            search=search,
            max_attempts=1,
        )
        self.assertEqual(len(result.candidate_telemetry), 2)
        self.assertTrue(result.candidate_telemetry[0].fetched)
        self.assertEqual(result.candidate_telemetry[1].rejection_code, "ATTEMPT_LIMIT_REACHED")
        self.assertFalse(result.candidate_telemetry[1].fetched)


if __name__ == "__main__":
    unittest.main()
