import unittest

from src.product_intelligence.candidate_ranking import CandidateRanking
from src.product_intelligence.catalog_input import CatalogInputRow
from src.product_intelligence.manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    RetrievedPayload,
)
from src.product_intelligence.runtime_policy import (
    RuntimeAuthorityEvidence,
    RuntimeDomainCandidate,
    resolve_identity_and_source_policy,
)
from src.product_intelligence.source_discovery import InMemorySourceSearchProvider, SearchResult


def row() -> CatalogInputRow:
    return CatalogInputRow(
        Mfg_Part_Num="RUNTIME-1",
        Part_Desc="runtime fixture",
        E1_Brand="-- Unbranded --",
        Unilog_Brand="-- No Unilog Brand --",
        DIB_Brand="-- No DIB Brand --",
        Part_Manuf="Unknown distributor",
    )


def ranked_candidate(domain: str, score: int) -> RuntimeDomainCandidate:
    return RuntimeDomainCandidate(
        domain=domain,
        ranking=CandidateRanking(
            decision="plausible",
            score=score,
            page_type="product",
            visible_mpn_match=True,
            identity_match=False,
        ),
    )


class RuntimeCandidateSchedulingTests(unittest.TestCase):
    def resolve(self, domains, urls_by_domain, fetcher, *, max_attempts=3, max_domains=3):
        search = InMemorySourceSearchProvider({
            f'site:{domain} "RUNTIME-1"': [SearchResult(url=url)]
            for domain, url in urls_by_domain.items()
        })
        return resolve_identity_and_source_policy(
            row(),
            search_provider=search,
            enrichment_provider=ManufacturerEnrichmentProvider(fetcher=fetcher),
            candidate_domain_provider=lambda _row: list(domains),
            site_identity_verifier=lambda _row, candidate, source, _text: None,
            max_source_attempts=max_attempts,
            max_candidate_domains=max_domains,
        )

    def test_round_robin_gives_each_domain_one_attempt_first(self) -> None:
        fetched = []
        domains = [
            ranked_candidate("alpha.example", 10),
            ranked_candidate("beta.example", 8),
            ranked_candidate("gamma.example", 6),
        ]
        urls = {
            "alpha.example": "https://alpha.example/product/RUNTIME-1",
            "beta.example": "https://beta.example/product/RUNTIME-1",
            "gamma.example": "https://gamma.example/product/RUNTIME-1",
        }

        def fetcher(url, _timeout):
            fetched.append(url)
            return RetrievedPayload(200, {"content-type": "text/html"}, b"RUNTIME-1")

        self.resolve(domains, urls, fetcher)
        self.assertEqual(fetched, list(urls.values()))

    def test_global_attempt_limit_remains_three(self) -> None:
        fetched = []
        domains = [ranked_candidate(f"d{n}.example", 8) for n in range(4)]
        urls = {candidate.domain: f"https://{candidate.domain}/RUNTIME-1" for candidate in domains}

        def fetcher(url, _timeout):
            fetched.append(url)
            return RetrievedPayload(200, {"content-type": "text/html"}, b"RUNTIME-1")

        result = self.resolve(domains, urls, fetcher, max_domains=4)
        self.assertEqual(len(fetched), 3)
        self.assertEqual(
            sum(item.rejection_code == "ATTEMPT_LIMIT_REACHED" for item in result.candidate_telemetry),
            1,
        )

    def test_single_domain_preserves_sequential_order(self) -> None:
        fetched = []
        domain = ranked_candidate("only.example", 8)
        urls = {
            "only.example": "https://only.example/product/RUNTIME-1",
        }
        search = InMemorySourceSearchProvider({
            'site:only.example "RUNTIME-1"': [
                SearchResult(url="https://only.example/product/one/RUNTIME-1"),
                SearchResult(url="https://only.example/product/two/RUNTIME-1"),
                SearchResult(url="https://only.example/product/three/RUNTIME-1"),
            ]
        })

        def fetcher(url, _timeout):
            fetched.append(url)
            return RetrievedPayload(200, {"content-type": "text/html"}, b"RUNTIME-1")

        resolve_identity_and_source_policy(
            row(),
            search_provider=search,
            enrichment_provider=ManufacturerEnrichmentProvider(fetcher=fetcher),
            candidate_domain_provider=lambda _row: [domain],
            site_identity_verifier=lambda _row, _candidate, _source, _text: None,
            max_source_attempts=3,
        )
        self.assertEqual(
            fetched,
            [
                "https://only.example/product/one/RUNTIME-1",
                "https://only.example/product/two/RUNTIME-1",
                "https://only.example/product/three/RUNTIME-1",
            ],
        )

    def test_higher_ranked_domain_is_still_first(self) -> None:
        fetched = []
        high = ranked_candidate("high.example", 20)
        low = ranked_candidate("low.example", 1)
        urls = {
            "high.example": "https://high.example/product/RUNTIME-1",
            "low.example": "https://low.example/product/RUNTIME-1",
        }

        def fetcher(url, _timeout):
            fetched.append(url)
            return RetrievedPayload(200, {"content-type": "text/html"}, b"RUNTIME-1")

        self.resolve([high, low], urls, fetcher, max_attempts=1)
        self.assertEqual(fetched, [urls["high.example"]])

    def test_rejected_candidate_still_consumes_attempt(self) -> None:
        fetched = []
        domains = [
            ranked_candidate("first.example", 10),
            ranked_candidate("second.example", 8),
            ranked_candidate("third.example", 6),
        ]
        urls = {
            candidate.domain: f"https://{candidate.domain}/RUNTIME-1"
            for candidate in domains
        }

        def fetcher(url, _timeout):
            fetched.append(url)
            return RetrievedPayload(200, {"content-type": "text/html"}, b"not-the-mpn")

        result = self.resolve(domains, urls, fetcher)
        self.assertEqual(len(fetched), 3)
        self.assertEqual(sum(item.fetched for item in result.candidate_telemetry), 3)


if __name__ == "__main__":
    unittest.main()
