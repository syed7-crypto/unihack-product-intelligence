import unittest

from src.product_intelligence.catalog_input import CatalogInputRow, INPUT_COLUMNS
from src.product_intelligence.catalogue_batch import run_catalogue_batch
from src.product_intelligence.catalogue_enrichment import CatalogueEnrichmentResult, _resolve_references
from src.product_intelligence.delivery_schema import DeliverySchema
from src.product_intelligence.manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    RetrievedPayload,
)
from src.product_intelligence.reference_data import BrandReference, ManufacturerReference
from src.product_intelligence.review import ReviewReport
from src.product_intelligence.runtime_policy import (
    IdentityResolutionResult,
    RuntimeDomainCandidate,
    RuntimeAuthorityEvidence,
    _catalogue_identity_candidates,
    _catalogue_identity_hint,
    _identity_matches,
    _runtime_discovery_queries,
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
    def test_catalogue_identity_hint_precedence_and_raw_manufacturer_preservation(self) -> None:
        milwaukee = row("49-94-0013", manufacturer="Milwaukee Accessory (4031)")
        self.assertEqual(_catalogue_identity_hint(milwaukee), "Milwaukee")
        self.assertEqual(milwaukee.Part_Manuf, "Milwaukee Accessory (4031)")
        self.assertEqual(
            _catalogue_identity_candidates(milwaukee),
            ("Milwaukee", "Milwaukee Accessory"),
        )

        timbertech = row("ADCB15516BS", manufacturer="Parksite (6151)")
        timbertech.DIB_Brand = "TIMBERTECH"
        self.assertEqual(_catalogue_identity_hint(timbertech), "TIMBERTECH")
        self.assertEqual(timbertech.Part_Manuf, "Parksite (6151)")

        philips = row("576512", manufacturer="Phillips Lighting (5831)")
        philips.DIB_Brand = "Philips"
        self.assertEqual(_catalogue_identity_hint(philips), "Philips")

        festool = row("578808", manufacturer="Festool USA (FESTO)")
        self.assertEqual(_catalogue_identity_hint(festool), "Festool")
        self.assertNotIn("FESTO", _catalogue_identity_candidates(festool))

    def test_parsed_milwaukee_hint_supports_page_identity_verification(self) -> None:
        catalogue = row(
            "49-94-0013",
            manufacturer="Milwaukee Accessory (4031)",
        )
        url = "https://milwaukee.example/product/49-94-0013"
        body = (
            b"<title>5 inch Metal Cut Off Wheel | Milwaukee Tool</title>"
            b'<meta property="og:title" content="Metal Cut Off Wheel | Milwaukee Tool">'
            b"<h1>5 inch Metal Cut Off Wheel</h1>"
            b"<p>Milwaukee Tool 49-94-0013</p>"
        )
        result = resolve_identity_and_source_policy(
            catalogue,
            search_provider=InMemorySourceSearchProvider({
                'site:milwaukee.example "49-94-0013"': [SearchResult(url=url)]
            }),
            enrichment_provider=self.provider([], body),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(
                domain="milwaukee.example"
            )],
        )

        self.assertEqual(result.state, "resolvable")
        self.assertEqual(result.resolved_identity, "Milwaukee")
        self.assertEqual(catalogue.Part_Manuf, "Milwaukee Accessory (4031)")

    def test_explicit_resolved_references_take_precedence_over_raw_hints(self) -> None:
        catalogue = row("REFERENCE-1", manufacturer="Approved Manufacturer")
        catalogue.DIB_Brand = "Approved Brand"
        self.assertEqual(
            _catalogue_identity_hint(
                catalogue,
                manufacturer_reference=ManufacturerReference(["Approved Manufacturer"]),
                brand_reference=BrandReference(["Approved Brand"]),
            ),
            "Approved Manufacturer",
        )

        catalogue.Part_Manuf = "Raw Distributor (123)"
        self.assertEqual(
            _catalogue_identity_hint(
                catalogue,
                manufacturer_reference=ManufacturerReference(["Approved Manufacturer"]),
                brand_reference=BrandReference(["Approved Brand"]),
            ),
            "Approved Brand",
        )

    def test_identity_matching_is_case_insensitive(self) -> None:
        self.assertTrue(_identity_matches("TIMBERTECH", "TimberTech"))
        self.assertTrue(_identity_matches("MILWAUKEE TOOL", "Milwaukee Tool"))

    def test_identity_matching_preserves_compact_punctuation_normalization(self) -> None:
        self.assertTrue(_identity_matches("A.C.M.E. Tools", "ACME-Tools"))
        self.assertTrue(_identity_matches("  Acme   Tools ", "ACME Tools"))
        self.assertFalse(_identity_matches("Festool", "Festo"))

    def test_chinook_style_page_resolves_uppercase_catalogue_brand(self) -> None:
        url = "https://chinook.example/product/ADCB15516BS"
        body = (
            b'<title>TimberTech Advanced - Harvest - Brownstone</title>'
            b'<meta property="og:title" content="TimberTech Advanced - Harvest">'
            b'<meta property="og:description" content="Azek Building Products">'
            b'<h1>TimberTech Advanced - Harvest</h1>'
            b'<div class="product-brandname">TimberTech</div>'
            b'<span>Product Code: ADCB15516BS</span>'
        )
        catalogue = CatalogInputRow(
            Mfg_Part_Num="ADCB15516BS",
            Part_Desc="1x6 Brownstone Harvest Azek PVC Decking",
            E1_Brand="TIMBERTECH",
            Unilog_Brand="-- No Unilog Brand --",
            DIB_Brand="-- No DIB Brand --",
            Part_Manuf="Parksite (6151)",
        )
        result = resolve_identity_and_source_policy(
            catalogue,
            search_provider=InMemorySourceSearchProvider({
                'site:chinook.example "ADCB15516BS"': [SearchResult(url=url)]
            }),
            enrichment_provider=self.provider([], body),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(
                domain="chinook.example"
            )],
        )

        self.assertEqual(result.state, "resolvable")
        self.assertEqual(result.resolved_identity, "TIMBERTECH")

    def provider(self, fetched: list[str], body: bytes = b"<h1>Model RUNTIME-1</h1>"):
        def fetcher(url: str, timeout: float) -> RetrievedPayload:
            fetched.append(url)
            return RetrievedPayload(200, {"content-type": "text/html"}, body)

        return ManufacturerEnrichmentProvider(fetcher=fetcher)

    def test_runtime_identity_resolves_without_copying_raw_manufacturer(self) -> None:
        runtime = IdentityResolutionResult(
            state="resolvable",
            resolved_identity="Runtime Manufacturer",
            identity_kind="manufacturer",
            approved_domains=("runtime.example",),
            reason="Test-only attestation.",
        )
        resolution = _resolve_references(row(), None, None, runtime)
        self.assertEqual(resolution.manufacturer.status, "resolved")
        self.assertEqual(resolution.manufacturer.resolved_value, "Runtime Manufacturer")
        self.assertEqual(resolution.manufacturer.input_value, "Runtime Manufacturer")
        self.assertNotEqual(resolution.manufacturer.resolved_value, row().Part_Manuf)
        self.assertIsNotNone(resolution.runtime_identity)

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

    def test_discovered_manufacturer_conflict_is_not_silently_overwritten(self) -> None:
        catalogue = row("578808", manufacturer="Festool USA (FESTO)")
        url = "https://festo.example/product/578808"
        result = resolve_identity_and_source_policy(
            catalogue,
            search_provider=InMemorySourceSearchProvider(
                {"578808": [SearchResult(url=url)]}
            ),
            enrichment_provider=self.provider([], b"<h1>Festo 578808</h1>"),
            authority_verifier=lambda _row, _candidate: RuntimeAuthorityEvidence(
                controlled_identity="Festo",
                identity_kind="manufacturer",
                domain="festo.example",
                reason="Verified test manufacturer identity.",
            ),
        )

        self.assertEqual(result.state, "unknown")
        self.assertEqual(result.failure_code, "MANUFACTURER_IDENTITY_CONFLICT")
        self.assertIn("Festool USA", result.diagnostics[0])
        self.assertEqual(catalogue.Part_Manuf, "Festool USA (FESTO)")

    def test_discovered_manufacturer_matching_normalized_catalogue_identity_is_allowed(self) -> None:
        catalogue = row("RUNTIME-1", manufacturer="Acme Tools (ACME)")
        url = "https://acme.example/product/RUNTIME-1"
        result = resolve_identity_and_source_policy(
            catalogue,
            search_provider=InMemorySourceSearchProvider(
                {"RUNTIME-1": [SearchResult(url=url)]}
            ),
            enrichment_provider=self.provider([]),
            authority_verifier=lambda _row, _candidate: RuntimeAuthorityEvidence(
                controlled_identity="Acme Tools",
                identity_kind="manufacturer",
                domain="acme.example",
                reason="Verified test manufacturer identity.",
            ),
        )

        self.assertEqual(result.state, "resolvable")
        self.assertEqual(result.resolved_identity, "Acme Tools")

    def test_conflicting_runtime_identity_becomes_review_in_batch(self) -> None:
        catalogue = row("578808", manufacturer="Festool USA")
        result = run_catalogue_batch(
            [catalogue],
            schema(),
            discovery_enabled=True,
            runtime_policy_resolution_enabled=True,
            search_provider=InMemorySourceSearchProvider(
                {"578808": [SearchResult(url="https://festo.example/product/578808")]}
            ),
            provider=self.provider([], b"<h1>Festo 578808</h1>"),
            runtime_authority_verifier=lambda _row, _candidate: RuntimeAuthorityEvidence(
                controlled_identity="Festo",
                identity_kind="manufacturer",
                domain="festo.example",
                reason="Verified test manufacturer identity.",
            ),
        )

        self.assertEqual(result.row_results[0].review.status, "needs_review")
        self.assertIn(
            "MANUFACTURER_IDENTITY_CONFLICT",
            {issue.code for issue in result.row_results[0].review.issues},
        )
        self.assertEqual(result.row_results[0].catalogue_row.Part_Manuf, "Festool USA")

    def test_product_first_rejects_first_domain_and_tries_second(self) -> None:
        first = "https://first.example/product/RUNTIME-1"
        second = "https://second.example/product/RUNTIME-1"
        search = InMemorySourceSearchProvider({
            'site:first.example "RUNTIME-1"': [SearchResult(url=first)],
            'site:second.example "RUNTIME-1"': [SearchResult(url=second)],
        })
        fetched: list[str] = []

        def fetcher(url: str, timeout: float) -> RetrievedPayload:
            fetched.append(url)
            body = (
                b"<title>First Company</title>"
                if url == first
                else b"<title>Second Manufacturer</title><h1>Manufacturer: Second Manufacturer</h1><p>RUNTIME-1</p>"
            )
            return RetrievedPayload(200, {"content-type": "text/html"}, body)

        result = resolve_identity_and_source_policy(
            row(), search_provider=search,
            enrichment_provider=ManufacturerEnrichmentProvider(fetcher=fetcher),
            candidate_domain_provider=lambda _row: [
                RuntimeDomainCandidate(domain="first.example", identity_hint="First Manufacturer"),
                RuntimeDomainCandidate(domain="second.example", identity_hint="Second Manufacturer"),
            ],
        )
        self.assertEqual(result.state, "resolvable")
        self.assertEqual(result.resolved_identity, "Second Manufacturer")
        self.assertEqual(fetched, [first, second])

    def test_product_first_uses_actual_page_identity_not_search_snippet(self) -> None:
        url = "https://official.example/product/RUNTIME-1"
        search = InMemorySourceSearchProvider({
            'site:official.example "RUNTIME-1"': [
                SearchResult(url=url, title="Untrusted Manufacturer", snippet="Untrusted Manufacturer")
            ]
        })
        result = resolve_identity_and_source_policy(
            row(), search_provider=search, enrichment_provider=self.provider([]),
            candidate_domain_provider=lambda _row: [
                RuntimeDomainCandidate(domain="official.example", identity_hint="Expected Manufacturer")
            ],
        )
        self.assertEqual(result.state, "unknown")
        self.assertIsNone(result.runtime_policy)

    def test_multiple_coherent_page_local_signals_establish_catalogue_brand(self) -> None:
        url = "https://philips.example/product/RUNTIME-1"
        body = (
            b"<html><head>"
            b'<title>Philips LED Product</title>'
            b'<meta property="og:title" content="Philips LED Product">'
            b'<meta property="og:site_name" content="Philips lighting">'
            b"</head><body><h1>Philips LED Product</h1>"
            b"<p>Model RUNTIME-1</p></body></html>"
        )
        candidate = row("RUNTIME-1")
        candidate.DIB_Brand = "Philips"
        result = resolve_identity_and_source_policy(
            candidate,
            search_provider=InMemorySourceSearchProvider({
                'site:philips.example "RUNTIME-1"': [SearchResult(url=url)]
            }),
            enrichment_provider=self.provider([], body),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(domain="philips.example")],
        )

        self.assertEqual(result.state, "resolvable")
        self.assertEqual(result.resolved_identity, "Philips")
        self.assertIn("title", result.runtime_policy.governance_reason if result.runtime_policy else "")

    def test_domain_alone_does_not_establish_identity(self) -> None:
        url = "https://philips.example/product/RUNTIME-1"
        result = resolve_identity_and_source_policy(
            row(),
            search_provider=InMemorySourceSearchProvider({
                'site:philips.example "RUNTIME-1"': [SearchResult(url=url)]
            }),
            enrichment_provider=self.provider([], b"<h1>RUNTIME-1</h1>"),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(
                domain="philips.example", identity_hint="philips.example"
            )],
        )

        self.assertEqual(result.state, "unknown")
        self.assertIsNone(result.runtime_policy)

    def test_conflicting_unlabeled_page_identity_is_not_resolved_by_signal_count(self) -> None:
        url = "https://candidate.example/product/RUNTIME-1"
        body = (
            b"<title>Philips LED Product</title>"
            b'<meta property="og:site_name" content="Philips lighting">'
            b"<h1>Philips LED Product</h1>"
            b"<p>Manufacturer: Festo</p><p>Festo product Model RUNTIME-1</p>"
        )
        candidate = row("RUNTIME-1")
        candidate.DIB_Brand = "Philips"
        result = resolve_identity_and_source_policy(
            candidate,
            search_provider=InMemorySourceSearchProvider({
                'site:candidate.example "RUNTIME-1"': [SearchResult(url=url)]
            }),
            enrichment_provider=self.provider([], body),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(domain="candidate.example")],
        )

        self.assertEqual(result.state, "unknown")
        self.assertEqual(result.failure_code, "MANUFACTURER_IDENTITY_CONFLICT")
        self.assertTrue(any("Catalogue identity conflicts" in item for item in result.diagnostics))

    def test_arbitrary_body_word_smart_cannot_become_identity(self) -> None:
        url = "https://philips.example/product/RUNTIME-1"
        body = (
            b"<title>Philips LED Product</title>"
            b'<meta property="og:site_name" content="Philips lighting">'
            b"<h1>Philips LED Product</h1><p>Smart product Model RUNTIME-1</p>"
        )
        candidate = row("RUNTIME-1")
        candidate.DIB_Brand = "Philips"
        result = resolve_identity_and_source_policy(
            candidate,
            search_provider=InMemorySourceSearchProvider({
                'site:philips.example "RUNTIME-1"': [SearchResult(url=url)]
            }),
            enrichment_provider=self.provider([], body),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(domain="philips.example")],
        )

        self.assertEqual(result.state, "resolvable")
        self.assertEqual(result.resolved_identity, "Philips")
        self.assertNotEqual(result.resolved_identity, "Smart")

    def test_arbitrary_body_word_this_cannot_become_identity(self) -> None:
        url = "https://timbertech.example/product/RUNTIME-1"
        body = (
            b"<title>TimberTech Decking Product</title>"
            b'<meta property="og:site_name" content="TimberTech">'
            b"<h1>TimberTech Decking</h1><p>This product is durable. Model RUNTIME-1</p>"
        )
        candidate = row("RUNTIME-1")
        candidate.DIB_Brand = "TIMBERTECH"
        result = resolve_identity_and_source_policy(
            candidate,
            search_provider=InMemorySourceSearchProvider({
                'site:timbertech.example "RUNTIME-1"': [SearchResult(url=url)]
            }),
            enrichment_provider=self.provider([], body),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(domain="timbertech.example")],
        )

        self.assertEqual(result.state, "resolvable")
        self.assertEqual(result.resolved_identity.casefold(), "timbertech")
        self.assertNotEqual(result.resolved_identity, "This")

    def test_generic_description_alone_cannot_establish_manufacturer(self) -> None:
        url = "https://unknown.example/product/RUNTIME-1"
        body = b"<title>Product Details</title><p>This product is useful. Model RUNTIME-1</p>"
        candidate = row("RUNTIME-1")
        candidate.DIB_Brand = "Expected Brand"
        result = resolve_identity_and_source_policy(
            candidate,
            search_provider=InMemorySourceSearchProvider({
                'site:unknown.example "RUNTIME-1"': [SearchResult(url=url)]
            }),
            enrichment_provider=self.provider([], body),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(domain="unknown.example")],
        )

        self.assertEqual(result.state, "unknown")
        self.assertIsNone(result.resolved_identity)

    def test_product_first_accepts_explicit_parent_company_relationship(self) -> None:
        url = "https://dewalt.example/product/RUNTIME-1"
        search = InMemorySourceSearchProvider({
            'site:dewalt.example "RUNTIME-1"': [SearchResult(url=url)]
        })
        result = resolve_identity_and_source_policy(
            row(), search_provider=search, enrichment_provider=self.provider([]),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(
                domain="dewalt.example", identity_hint="DEWALT"
            )],
            site_identity_verifier=lambda _row, _candidate, source, text: RuntimeAuthorityEvidence(
                controlled_identity="DEWALT",
                identity_kind="brand",
                domain="dewalt.example",
                reason="Page identifies DEWALT as a brand of Stanley Black & Decker.",
            ) if "RUNTIME-1" in text else None,
        )
        self.assertEqual(result.state, "resolvable")
        self.assertEqual(result.identity_kind, "brand")

    def test_unrelated_site_identity_is_rejected(self) -> None:
        url = "https://candidate.example/product/RUNTIME-1"
        search = InMemorySourceSearchProvider({
            'site:candidate.example "RUNTIME-1"': [SearchResult(url=url)]
        })
        result = resolve_identity_and_source_policy(
            row(), search_provider=search, enrichment_provider=self.provider([], b"<h1>Other Company RUNTIME-1</h1>"),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(
                domain="candidate.example", identity_hint="Expected Manufacturer"
            )],
        )
        self.assertEqual(result.state, "unknown")

    def test_retailer_domain_is_rejected_before_product_retrieval(self) -> None:
        search = InMemorySourceSearchProvider({})
        fetched: list[str] = []
        result = resolve_identity_and_source_policy(
            row(), search_provider=search, enrichment_provider=self.provider(fetched),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(
                domain="www.amazon.com", identity_hint="Amazon"
            )],
        )
        self.assertEqual(result.state, "unknown")
        self.assertEqual(fetched, [])

    def test_candidate_limit_prevents_runaway_domain_search(self) -> None:
        search = InMemorySourceSearchProvider({})
        result = resolve_identity_and_source_policy(
            row(), search_provider=search, enrichment_provider=self.provider([]),
            candidate_domain_provider=lambda _row: [
                RuntimeDomainCandidate(domain=f"domain{i}.example", identity_hint=f"Company {i}")
                for i in range(5)
            ],
            max_candidate_domains=2,
        )
        self.assertEqual(result.state, "unknown")
        self.assertEqual(len(search.queries), 2)

    def test_unknown_runtime_resolution_is_needs_review_in_batch(self) -> None:
        result = run_catalogue_batch(
            [row()], schema(), discovery_enabled=True,
            runtime_policy_resolution_enabled=True,
            search_provider=InMemorySourceSearchProvider({"RUNTIME-1": []}),
        )
        self.assertEqual(result.needs_review_rows, 1)
        self.assertEqual(result.row_results[0].review.status, "needs_review")
        self.assertEqual(result.delivery_rows[0]["Mfg_Part_Num"], "RUNTIME-1")

    def test_runtime_queries_include_manufacturer_and_brand_for_philips(self) -> None:
        catalogue = row("576512")
        catalogue.Part_Manuf = "Phillips Lighting (5831)"
        catalogue.DIB_Brand = "Philips"
        search = InMemorySourceSearchProvider({})

        result = resolve_identity_and_source_policy(
            catalogue,
            search_provider=search,
            enrichment_provider=self.provider([]),
        )

        self.assertEqual(result.state, "unknown")
        self.assertEqual(
            [query for query, _limit in search.queries],
            [
                "576512 Phillips Lighting",
                "576512 Philips",
                "576512",
                "576512 Philips distributor",
            ],
        )

    def test_runtime_queries_include_manufacturer_for_festool(self) -> None:
        catalogue = row("578808")
        catalogue.Part_Manuf = "Festool USA (FESTO)"
        search = InMemorySourceSearchProvider({})

        resolve_identity_and_source_policy(
            catalogue,
            search_provider=search,
            enrichment_provider=self.provider([]),
        )

        self.assertEqual(
            [query for query, _limit in search.queries],
            [
                "578808 Festool USA",
                "578808",
                "578808 manufacturer",
                "578808 product",
            ],
        )

    def test_runtime_query_falls_back_to_mpn_without_identity(self) -> None:
        catalogue = row("NO-IDENTITY")
        catalogue.Part_Manuf = ""
        search = InMemorySourceSearchProvider({})

        resolve_identity_and_source_policy(
            catalogue,
            search_provider=search,
            enrichment_provider=self.provider([]),
        )

        self.assertEqual(
            [query for query, _limit in search.queries],
            ["NO-IDENTITY", "NO-IDENTITY manufacturer", "NO-IDENTITY product"],
        )

    def test_weak_manufacturer_hint_gets_bounded_search_variants(self) -> None:
        catalogue = row("3MABR-7100075678", manufacturer="Jam Industrial Supply LLC (JAMIN)")
        search = InMemorySourceSearchProvider({})

        resolve_identity_and_source_policy(
            catalogue,
            search_provider=search,
            enrichment_provider=self.provider([]),
        )

        queries = [query for query, _limit in search.queries]
        self.assertEqual(
            queries,
            [
                "3MABR-7100075678 Jam Industrial Supply LLC",
                "3MABR-7100075678",
                "3MABR-7100075678 manufacturer",
                "3MABR-7100075678 product",
            ],
        )
        self.assertTrue(all("3MABR-7100075678" in query for query in queries))

    def test_strong_brand_hint_does_not_receive_generic_expansion(self) -> None:
        catalogue = row("ADCB15516BS", manufacturer="Parksite (6151)")
        catalogue.E1_Brand = "TIMBERTECH"
        search = InMemorySourceSearchProvider({})

        resolve_identity_and_source_policy(
            catalogue,
            search_provider=search,
            enrichment_provider=self.provider([]),
        )

        queries = [query for query, _limit in search.queries]
        self.assertEqual(
            queries,
            [
                "ADCB15516BS Parksite",
                "ADCB15516BS TIMBERTECH",
                "ADCB15516BS",
                "ADCB15516BS TIMBERTECH distributor",
            ],
        )
        self.assertFalse(any(query.endswith(" manufacturer") for query in queries))

    def test_targeted_ecosystem_queries_are_bounded_and_mpn_scoped(self) -> None:
        timbertech = row("ADCB15516BS", manufacturer="Parksite (6151)")
        timbertech.E1_Brand = "TIMBERTECH"
        trex = row("1513724", manufacturer="Boise Cascade Building Materials (BOICA)")
        trex.E1_Brand = "TREX"
        united = row("1517603", manufacturer="United Window & Door Manufacturing (UNIWI)")
        united.E1_Brand = "United Window & Door"

        timbertech_queries = _runtime_discovery_queries(timbertech)
        trex_queries = _runtime_discovery_queries(trex)
        united_queries = _runtime_discovery_queries(united)

        self.assertEqual(
            timbertech_queries,
            [
                "ADCB15516BS Parksite",
                "ADCB15516BS TIMBERTECH",
                "ADCB15516BS",
                "ADCB15516BS TIMBERTECH distributor",
            ],
        )
        self.assertEqual(
            trex_queries,
            [
                "1513724 Boise Cascade Building Materials",
                "1513724 TREX",
                "1513724",
                'site:trex.com "1513724"',
                'site:www.trex.com "1513724"',
            ],
        )
        self.assertEqual(
            united_queries,
            [
                "1517603 United Window & Door Manufacturing",
                "1517603 United Window & Door",
                "1517603",
                "1517603 United Window & Door distributor",
            ],
        )
        for expected_mpn, queries in (
            ("ADCB15516BS", timbertech_queries),
            ("1513724", trex_queries),
            ("1517603", united_queries),
        ):
            self.assertLessEqual(len(queries), 7)
            self.assertEqual(len(queries), len({query.casefold() for query in queries}))
            self.assertTrue(all(expected_mpn in query for query in queries))

    def test_site_queries_require_controlled_domain_mapping(self) -> None:
        unknown = row("UNKNOWN-1", manufacturer="Not A Controlled Company")
        unknown.E1_Brand = "Not A Controlled Brand"
        queries = _runtime_discovery_queries(unknown)

        self.assertFalse(any(query.startswith("site:") for query in queries))
        self.assertTrue(all("UNKNOWN-1" in query for query in queries))

    def test_query_variants_are_deduplicated(self) -> None:
        catalogue = row("DUP-1", manufacturer="Acme")
        catalogue.E1_Brand = "Acme"
        search = InMemorySourceSearchProvider({})

        resolve_identity_and_source_policy(
            catalogue,
            search_provider=search,
            enrichment_provider=self.provider([]),
        )

        queries = [query for query, _limit in search.queries]
        self.assertEqual(
            queries,
            ["DUP-1 Acme", "DUP-1", "DUP-1 Acme distributor"],
        )

    def test_runtime_ranking_skips_conflicting_candidate_before_domain_fetch(self) -> None:
        catalogue = row("COLLISION-2")
        catalogue.Part_Manuf = ""
        catalogue.E1_Brand = "United"
        catalogue.Part_Desc = "6068L Gliding Patio Door"
        bad_url = "https://bad.example/collections/accessories?mpn=COLLISION-2"
        good_url = "https://good.example/products/COLLISION-2"
        search = InMemorySourceSearchProvider({
            "COLLISION-2 United": [
                SearchResult(url=bad_url, title="Ariat Flag Cap COLLISION-2")
            ],
            "COLLISION-2": [
                SearchResult(url=good_url, title="United 6068L Patio Door COLLISION-2")
            ],
            'site:good.example "COLLISION-2"': [SearchResult(url=good_url)],
        })
        fetched: list[str] = []

        def fetcher(url: str, timeout: float) -> RetrievedPayload:
            fetched.append(url)
            return RetrievedPayload(
                200,
                {"content-type": "text/html"},
                b"<h1>United 6068L Gliding Patio Door COLLISION-2</h1>",
            )

        result = resolve_identity_and_source_policy(
            catalogue,
            search_provider=search,
            enrichment_provider=ManufacturerEnrichmentProvider(fetcher=fetcher),
            site_identity_verifier=lambda _row, _candidate, source, text: RuntimeAuthorityEvidence(
                controlled_identity="United",
                identity_kind="manufacturer",
                domain=source.manufacturer_domain,
                reason="Deterministic test authority evidence.",
            ),
        )

        self.assertEqual(result.state, "resolvable")
        self.assertEqual([source.url for source in result.verified_sources], [good_url])
        self.assertEqual(fetched, [good_url])

    def test_equivalent_manufacturer_and_brand_queries_are_deduplicated(self) -> None:
        catalogue = row("DUPLICATE-1")
        catalogue.Part_Manuf = "Philips"
        catalogue.DIB_Brand = " philips "
        search = InMemorySourceSearchProvider({})

        resolve_identity_and_source_policy(
            catalogue,
            search_provider=search,
            enrichment_provider=self.provider([]),
        )

        self.assertEqual(
            [query for query, _limit in search.queries],
            [
                "DUPLICATE-1 Philips",
                "DUPLICATE-1",
                "DUPLICATE-1 philips distributor",
            ],
        )

    def test_completed_search_without_trustworthy_source_is_not_retrieval_failure(self) -> None:
        result = resolve_identity_and_source_policy(
            row(),
            search_provider=InMemorySourceSearchProvider({"RUNTIME-1": []}),
            enrichment_provider=self.provider([]),
        )

        self.assertEqual(result.state, "unknown")
        self.assertEqual(result.failure_code, "NO_TRUSTWORTHY_SOURCE")

    def test_transport_failure_is_reported_as_source_retrieval_failed(self) -> None:
        url = "https://runtime.example/product/RUNTIME-1"

        def failing_fetcher(_url: str, _timeout: float) -> RetrievedPayload:
            raise TimeoutError("DNS lookup timed out")

        result = run_catalogue_batch(
            [row()], schema(), discovery_enabled=True,
            runtime_policy_resolution_enabled=True,
            search_provider=InMemorySourceSearchProvider({
                'site:runtime.example "RUNTIME-1"': [SearchResult(url=url)]
            }),
            provider=ManufacturerEnrichmentProvider(fetcher=failing_fetcher),
            runtime_candidate_domain_provider=lambda _row: [
                RuntimeDomainCandidate(domain="runtime.example", identity_hint="Runtime")
            ],
        )

        self.assertEqual(result.blocked_rows, 1)
        self.assertIn(
            "SOURCE_RETRIEVAL_FAILED",
            {issue.issue.code for issue in result.review_issues},
        )
        self.assertEqual(result.delivery_rows[0]["Mfg_Part_Num"], "RUNTIME-1")

    def test_resolvable_runtime_policy_reaches_batch_enricher(self) -> None:
        url = "https://runtime.example/product/RUNTIME-1"
        captured: list[object] = []
        captured_identity: list[object] = []

        def enricher(catalogue_row, _urls, delivery_schema, **kwargs):
            captured.extend(kwargs.get("verified_sources") or ())
            captured_identity.append(kwargs.get("runtime_identity"))
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
            provider=self.provider([], b"<h1>Brand: Hunter</h1><p>RUNTIME-1</p>"),
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
        self.assertEqual(captured_identity[0].resolved_identity, "Runtime Manufacturer")
        self.assertEqual(captured_identity[0].identity_kind, "manufacturer")

    def test_product_first_runtime_policy_reaches_batch_enricher(self) -> None:
        url = "https://runtime.example/product/RUNTIME-1"
        captured: list[object] = []
        captured_identity: list[object] = []

        def enricher(catalogue_row, _urls, delivery_schema, **kwargs):
            captured.extend(kwargs.get("verified_sources") or ())
            captured_identity.append(kwargs.get("runtime_identity"))
            return CatalogueEnrichmentResult(
                catalogue_row=catalogue_row,
                pipeline_result=None,
                delivery_row=delivery_schema.empty_row(),
                review=ReviewReport(status="ready"),
            )

        result = run_catalogue_batch(
            [row()], schema(), discovery_enabled=True,
            runtime_policy_resolution_enabled=True,
            search_provider=InMemorySourceSearchProvider({
                'site:runtime.example "RUNTIME-1"': [SearchResult(url=url)]
            }),
            provider=self.provider([], b"<h1>Brand: Hunter</h1><p>RUNTIME-1 runtime fixture</p>"),
            runtime_candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(
                domain="runtime.example", identity_hint="Hunter"
            )],
            row_enricher=enricher,
        )
        self.assertEqual(result.ready_rows, 1)
        self.assertEqual([source.url for source in captured], [url])
        self.assertEqual(captured_identity[0].resolved_identity, "Hunter")
        self.assertEqual(captured_identity[0].identity_kind, "brand")

    def test_product_first_accepts_compact_brand_format_from_page_text(self) -> None:
        url = "https://milwaukeetool.example/product/RUNTIME-1"
        result = resolve_identity_and_source_policy(
            row(),
            search_provider=InMemorySourceSearchProvider({
                'site:milwaukeetool.example "RUNTIME-1"': [SearchResult(url=url)]
            }),
            enrichment_provider=self.provider([], b"<h1>Brand: Milwaukee Tool</h1><p>RUNTIME-1</p>"),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(
                domain="milwaukeetool.example", identity_hint="milwaukeetool"
            )],
        )
        self.assertEqual(result.state, "resolvable")
        self.assertEqual(result.resolved_identity, "Milwaukee Tool")

    def test_retailer_domain_uses_page_manufacturer_not_hostname(self) -> None:
        catalogue = row("RUNTIME-1")
        catalogue.DIB_Brand = "Mirka"
        result = resolve_identity_and_source_policy(
            catalogue,
            search_provider=InMemorySourceSearchProvider({
                'site:beavertools.example "RUNTIME-1"': [
                    SearchResult(url="https://beavertools.example/product/RUNTIME-1")
                ]
            }),
            enrichment_provider=self.provider(
                [], b"<h1>Brand: Mirka</h1><p>RUNTIME-1</p>"
            ),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(
                domain="beavertools.example", identity_hint="beavertools"
            )],
        )

        self.assertEqual(result.state, "resolvable")
        self.assertEqual(result.resolved_identity, "Mirka")

    def test_official_domain_hostname_is_not_used_as_identity(self) -> None:
        catalogue = row("RUNTIME-1")
        catalogue.DIB_Brand = "Milwaukee"
        result = resolve_identity_and_source_policy(
            catalogue,
            search_provider=InMemorySourceSearchProvider({
                'site:milwaukeetool.example "RUNTIME-1"': [
                    SearchResult(url="https://milwaukeetool.example/product/RUNTIME-1")
                ]
            }),
            enrichment_provider=self.provider(
                [], b"<h1>Brand: Milwaukee</h1><p>RUNTIME-1</p>"
            ),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(
                domain="milwaukeetool.example", identity_hint="milwaukeetool"
            )],
        )

        self.assertEqual(result.state, "resolvable")
        self.assertEqual(result.resolved_identity, "Milwaukee")
        self.assertNotEqual(result.resolved_identity, "milwaukeetool")

    def test_page_conflicting_manufacturer_still_creates_identity_conflict(self) -> None:
        catalogue = row("578808", manufacturer="Festool USA")
        result = resolve_identity_and_source_policy(
            catalogue,
            search_provider=InMemorySourceSearchProvider({
                'site:festo.example "578808"': [
                    SearchResult(url="https://festo.example/product/578808")
                ]
            }),
            enrichment_provider=self.provider(
                [], b"<h1>Manufacturer: Festo</h1><p>578808 runtime fixture</p>"
            ),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(
                domain="festo.example", identity_hint="festo"
            )],
        )

        self.assertEqual(result.state, "unknown")
        self.assertEqual(result.failure_code, "MANUFACTURER_IDENTITY_CONFLICT")

    def test_domain_only_identity_is_insufficient(self) -> None:
        result = resolve_identity_and_source_policy(
            row("RUNTIME-1"),
            search_provider=InMemorySourceSearchProvider({
                'site:beavertools.example "RUNTIME-1"': [
                    SearchResult(url="https://beavertools.example/product/RUNTIME-1")
                ]
            }),
            enrichment_provider=self.provider(
                [], b"<h1>beavertools RUNTIME-1</h1>"
            ),
            candidate_domain_provider=lambda _row: [RuntimeDomainCandidate(
                domain="beavertools.example", identity_hint="beavertools"
            )],
        )

        self.assertEqual(result.state, "unknown")
        self.assertNotEqual(result.resolved_identity, "beavertools")


if __name__ == "__main__":
    unittest.main()
