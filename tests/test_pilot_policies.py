import unittest

from src.product_intelligence.catalog_input import CatalogInputRow
from src.product_intelligence.manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    RetrievedPayload,
)
from src.product_intelligence.pilot_policies import get_pilot_source_policy
from src.product_intelligence.pilot_policies import (
    get_controlled_source_policy,
    resolve_source_policy,
    resolve_source_policy_for_row,
)
from src.product_intelligence.reference_data import BrandReference, ManufacturerReference
from src.product_intelligence.source_discovery import (
    InMemorySourceSearchProvider,
    ManufacturerSourcePolicy,
    SearchResult,
    discover_and_verify_sources,
    run_discovery_pilot,
)


def row(mpn: str) -> CatalogInputRow:
    return CatalogInputRow(
        Mfg_Part_Num=mpn,
        Part_Desc="pilot fixture",
        E1_Brand="-- Unbranded --",
        Unilog_Brand="-- No Unilog Brand --",
        DIB_Brand="",
        Part_Manuf="unresolved catalogue manufacturer",
    )


class PilotPolicyTests(unittest.TestCase):
    def test_manufacturer_policy_resolves_by_controlled_identity(self) -> None:
        policy = resolve_source_policy(manufacturer_identity="frigidaire")
        self.assertIsNotNone(policy)
        self.assertEqual(policy.manufacturer_name, "Frigidaire")

    def test_brand_policy_resolves_by_controlled_identity(self) -> None:
        policy = resolve_source_policy(brand_identity="dewalt")
        self.assertIsNotNone(policy)
        self.assertEqual(policy.manufacturer_name, "DEWALT")

    def test_manufacturer_identity_takes_precedence_over_brand_identity(self) -> None:
        policy = resolve_source_policy(
            manufacturer_identity="Frigidaire",
            brand_identity="DEWALT",
        )
        self.assertIsNotNone(policy)
        self.assertEqual(policy.manufacturer_name, "Frigidaire")

    def test_unknown_controlled_identity_is_unresolved(self) -> None:
        self.assertIsNone(resolve_source_policy(manufacturer_identity="Unknown Corp"))
        self.assertIsNone(resolve_source_policy(brand_identity="Unknown Brand"))

    def test_distributor_like_raw_manufacturer_is_not_trusted(self) -> None:
        self.assertIsNone(resolve_source_policy_for_row(row("NEW-PRODUCT")))

    def test_controlled_policy_contains_governance_metadata_only(self) -> None:
        policy = get_controlled_source_policy("Trex", "brand")
        self.assertIsNotNone(policy)
        assert policy is not None
        self.assertEqual(policy.controlled_identity, "Trex")
        self.assertEqual(policy.identity_kind, "brand")
        self.assertTrue(policy.governance_reason)
        serialized = policy.model_dump_json().casefold()
        self.assertNotIn("attribute_value", serialized)
        self.assertNotIn("delivery", serialized)
        self.assertNotIn("expected", serialized)

    def test_controlled_references_enable_generic_row_resolution(self) -> None:
        candidate = CatalogInputRow(
            Mfg_Part_Num="NEW-DEWALT-PRODUCT",
            Part_Desc="controlled fixture",
            E1_Brand="DEWALT",
            Unilog_Brand="",
            DIB_Brand="",
            Part_Manuf="unresolved catalogue distributor",
        )
        policy = resolve_source_policy_for_row(
            candidate,
            brand_reference=BrandReference(["DEWALT"]),
        )
        self.assertIsNotNone(policy)
        self.assertEqual(policy.manufacturer_name, "DEWALT")

    def test_six_pilot_products_resolve_through_compatibility_path(self) -> None:
        expected = {
            "PDSH4816AF": "Frigidaire",
            "59210": "Hunter",
            "S03-05226-IS": "Leviton",
            "KDFM404KPS": "KitchenAid",
            "DWST41092": "DEWALT",
            "543302126": "Trex",
        }
        for mpn, manufacturer in expected.items():
            with self.subTest(mpn=mpn):
                policy = resolve_source_policy_for_row(row(mpn))
                self.assertIsNotNone(policy)
                self.assertEqual(policy.manufacturer_name, manufacturer)

    def test_controlled_manufacturer_reference_has_priority_over_brand_reference(self) -> None:
        candidate = CatalogInputRow(
            Mfg_Part_Num="NEW-PRODUCT",
            Part_Desc="controlled fixture",
            E1_Brand="DEWALT",
            Unilog_Brand="",
            DIB_Brand="",
            Part_Manuf="Frigidaire",
        )
        policy = resolve_source_policy_for_row(
            candidate,
            manufacturer_reference=ManufacturerReference(["Frigidaire"]),
            brand_reference=BrandReference(["DEWALT"]),
        )
        self.assertIsNotNone(policy)
        self.assertEqual(policy.manufacturer_name, "Frigidaire")

    def test_three_selected_products_receive_expected_pilot_policies(self) -> None:
        frigidaire = get_pilot_source_policy("PDSH4816AF")
        hunter = get_pilot_source_policy("59210")
        leviton = get_pilot_source_policy("S03-05226-IS")

        self.assertEqual(frigidaire.manufacturer_name, "Frigidaire")
        self.assertEqual(hunter.manufacturer_name, "Hunter")
        self.assertEqual(leviton.manufacturer_name, "Leviton")

    def test_expanded_products_receive_expected_pilot_policies(self) -> None:
        expected = {
            "KDFM404KPS": ("KitchenAid", ("kitchenaid.com", "www.kitchenaid.com")),
            "DWST41092": ("DEWALT", ("dewalt.com", "www.dewalt.com")),
            "543302126": ("Trex", ("trex.com", "www.trex.com")),
        }
        for mpn, (manufacturer, domains) in expected.items():
            with self.subTest(mpn=mpn):
                policy = get_pilot_source_policy(mpn)
                self.assertIsNotNone(policy)
                assert policy is not None
                self.assertEqual(policy.manufacturer_name, manufacturer)
                self.assertEqual(policy.approved_domains, domains)

    def test_unknown_product_still_has_no_implicit_policy(self) -> None:
        self.assertIsNone(get_pilot_source_policy("not-a-pilot-product"))

    def test_domains_are_exact_and_contain_no_retailers(self) -> None:
        self.assertEqual(
            get_pilot_source_policy("PDSH4816AF").approved_domains,
            ("www.frigidaire.com", "frigidaire.com", "frigidaire.bynder.com"),
        )
        self.assertEqual(
            get_pilot_source_policy("59210").approved_domains,
            ("www.hunterfan.com", "hunterfan.com", "image.hunterfan.com"),
        )
        self.assertEqual(
            get_pilot_source_policy("S03-05226-IS").approved_domains,
            ("leviton.com", "content.leviton.com"),
        )
        self.assertEqual(
            get_pilot_source_policy("KDFM404KPS").approved_domains,
            ("kitchenaid.com", "www.kitchenaid.com"),
        )
        self.assertEqual(
            get_pilot_source_policy("DWST41092").approved_domains,
            ("dewalt.com", "www.dewalt.com"),
        )
        self.assertEqual(
            get_pilot_source_policy("543302126").approved_domains,
            ("trex.com", "www.trex.com"),
        )
        serialized = str(
            [
                get_pilot_source_policy("PDSH4816AF"),
                get_pilot_source_policy("59210"),
                get_pilot_source_policy("S03-05226-IS"),
                get_pilot_source_policy("KDFM404KPS"),
                get_pilot_source_policy("DWST41092"),
                get_pilot_source_policy("543302126"),
            ]
        ).casefold()
        self.assertNotIn("amazon", serialized)
        self.assertNotIn("ebay", serialized)
        self.assertNotIn("walmart", serialized)
        self.assertNotIn("homedepot", serialized)

    def test_registry_contains_governance_only_fields(self) -> None:
        policy = get_pilot_source_policy("PDSH4816AF")
        self.assertEqual(
            set(policy.model_dump()),
            {"manufacturer_name", "approved_domains", "allowed_source_kinds", "query_templates"},
        )
        self.assertNotIn("attribute", policy.model_dump_json().casefold())
        self.assertNotIn("delivery", policy.model_dump_json().casefold())

    def test_caller_supplied_policy_overrides_pilot_policy(self) -> None:
        supplied = ManufacturerSourcePolicy(
            manufacturer_name="Caller Controlled",
            approved_domains=("caller.example",),
        )

        reports = run_discovery_pilot(
            [row("PDSH4816AF")],
            policy_for_row=lambda _: supplied,
            search_provider=InMemorySourceSearchProvider({}),
            enrichment_provider=ManufacturerEnrichmentProvider(),
        )

        self.assertEqual(reports[0].discovery.status, "no_candidates")
        self.assertEqual(reports[0].discovery.queries[1], "PDSH4816AF Caller Controlled")

    def test_missing_policy_preserves_policy_missing_behavior(self) -> None:
        reports = run_discovery_pilot(
            [row("KDFM404KPS")],
            policy_for_row=lambda _: None,
            search_provider=InMemorySourceSearchProvider({}),
            enrichment_provider=ManufacturerEnrichmentProvider(),
        )

        self.assertIsNone(reports[0].discovery)
        self.assertIn("No explicit manufacturer source policy", reports[0].error)

    def test_pilot_policy_does_not_bypass_exact_mpn_verification(self) -> None:
        policy = get_pilot_source_policy("S03-05226-IS")
        search = InMemorySourceSearchProvider(
            {
                "S03-05226-IS": [
                    # A likely official-domain candidate whose content has only
                    # the related 5226-I identifier.
                    __import__("src.product_intelligence.source_discovery", fromlist=["SearchResult"]).SearchResult(
                        url="https://leviton.com/5226-I",
                        title="5226-I Switch",
                        snippet="5226-I",
                    )
                ]
            }
        )
        provider = ManufacturerEnrichmentProvider(
            approved_domains=set(policy.approved_domains),
            fetcher=lambda url, timeout: RetrievedPayload(
                200,
                {"content-type": "text/html"},
                b"Model 5226-I",
            ),
        )

        result = discover_and_verify_sources(row("S03-05226-IS"), policy, search, provider)

        self.assertEqual(result.verified_sources, [])
        self.assertEqual(result.diagnostics[0].verification_status, "failed")
        self.assertIn("Exact MPN", result.diagnostics[0].error or "")

    def test_hunter_policy_does_not_accept_similar_mpn(self) -> None:
        policy = get_pilot_source_policy("59210")
        from src.product_intelligence.source_discovery import SearchResult

        search = InMemorySourceSearchProvider(
            {"59210 Hunter": [SearchResult(url="https://hunterfan.com/59211", title="59211")]}
        )
        provider = ManufacturerEnrichmentProvider(
            approved_domains=set(policy.approved_domains),
            fetcher=lambda url, timeout: RetrievedPayload(
                200,
                {"content-type": "text/html"},
                b"Model 59211",
            ),
        )

        result = discover_and_verify_sources(row("59210"), policy, search, provider)

        self.assertEqual(result.verified_sources, [])

    def test_run_discovery_pilot_passes_selected_policy_domains_to_verifier(self) -> None:
        official_url = "https://hunterfan.com/59210"
        retailer_url = "https://example-retailer.com/59210"
        search_results = [
            SearchResult(url=official_url, title="Hunter 59210", snippet="59210"),
            SearchResult(url=retailer_url, title="59210", snippet="59210"),
        ]
        search = InMemorySourceSearchProvider(
            {
                "59210": search_results,
                "59210 Hunter": search_results,
            }
        )
        retrieved_urls: list[str] = []

        def fetcher(url: str, timeout: float) -> RetrievedPayload:
            retrieved_urls.append(url)
            return RetrievedPayload(
                200,
                {"content-type": "text/html"},
                b"<h1>Hunter model 59210</h1>",
            )

        # Deliberately start with the provider's default Frigidaire allowlist.
        # The selected Hunter policy must be scoped into verification.
        provider = ManufacturerEnrichmentProvider(fetcher=fetcher)
        reports = run_discovery_pilot(
            [row("59210")],
            search_provider=search,
            enrichment_provider=provider,
        )

        verification = reports[0].verification
        self.assertIsNotNone(verification)
        assert verification is not None
        self.assertEqual([source.url for source in verification.verified_sources], [official_url])
        self.assertEqual(retrieved_urls, [official_url])
        self.assertTrue(
            any(
                diagnostic.url == retailer_url
                and diagnostic.verification_status == "rejected"
                for diagnostic in verification.diagnostics
            )
        )


if __name__ == "__main__":
    unittest.main()
