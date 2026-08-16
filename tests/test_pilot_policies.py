import unittest

from src.product_intelligence.catalog_input import CatalogInputRow
from src.product_intelligence.manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    RetrievedPayload,
)
from src.product_intelligence.pilot_policies import get_pilot_source_policy
from src.product_intelligence.source_discovery import (
    InMemorySourceSearchProvider,
    ManufacturerSourcePolicy,
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
    def test_three_selected_products_receive_expected_pilot_policies(self) -> None:
        frigidaire = get_pilot_source_policy("PDSH4816AF")
        hunter = get_pilot_source_policy("59210")
        leviton = get_pilot_source_policy("S03-05226-IS")

        self.assertEqual(frigidaire.manufacturer_name, "Frigidaire")
        self.assertEqual(hunter.manufacturer_name, "Hunter")
        self.assertEqual(leviton.manufacturer_name, "Leviton")

    def test_remaining_selected_products_have_no_policy(self) -> None:
        for mpn in ("KDFM404KPS", "DWST41092", "543302126"):
            with self.subTest(mpn=mpn):
                self.assertIsNone(get_pilot_source_policy(mpn))

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
        serialized = str(
            [
                get_pilot_source_policy("PDSH4816AF"),
                get_pilot_source_policy("59210"),
                get_pilot_source_policy("S03-05226-IS"),
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


if __name__ == "__main__":
    unittest.main()
