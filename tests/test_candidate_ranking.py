import unittest

from src.product_intelligence.candidate_ranking import rank_candidate
from src.product_intelligence.review import build_review_report


class CandidateRankingTests(unittest.TestCase):
    def test_strong_approved_product_candidate_outranks_unapproved_retailer(self) -> None:
        official = rank_candidate(
            url="https://philips.example/products/576512",
            title="Philips 576512 LED BR40",
            snippet="65W LED bulb",
            expected_mpn="576512",
            expected_identities=("Philips",),
            approved_domain=True,
            exact_mpn_in_result=True,
        )
        retailer = rank_candidate(
            url="https://retailer.example/products/576512",
            title="Philips 576512 LED BR40",
            snippet="65W LED bulb",
            expected_mpn="576512",
            expected_identities=("Philips",),
            approved_domain=False,
            exact_mpn_in_result=True,
        )

        self.assertEqual(official.decision, "strong")
        self.assertEqual(retailer.decision, "plausible")
        self.assertGreater(official.score, retailer.score)

    def test_exact_mpn_with_conflicting_identity_is_not_strong(self) -> None:
        result = rank_candidate(
            url="https://shop.example/products/1517603",
            title="Ariat USA Flag Patch Navy Snapback Ball Cap 1517603",
            snippet="Snapback cap",
            expected_mpn="1517603",
            expected_identities=("United Window & Door",),
            expected_description="6068L Gliding Patio Door",
            approved_domain=True,
            exact_mpn_in_result=True,
        )

        self.assertEqual(result.decision, "bad")
        self.assertTrue(any("conflicts" in reason for reason in result.reasons))

    def test_product_page_outranks_collection_page(self) -> None:
        product = rank_candidate(
            url="https://philips.example/products/576512",
            title="Philips 576512 LED BR40",
            snippet="65W LED bulb",
            expected_mpn="576512",
            expected_identities=("Philips",),
            approved_domain=True,
            exact_mpn_in_result=True,
        )
        collection = rank_candidate(
            url="https://philips.example/collections/lighting?page=4",
            title="Philips lighting collection",
            snippet="576512 LED products",
            expected_mpn="576512",
            expected_identities=("Philips",),
            approved_domain=True,
            exact_mpn_in_result=True,
        )

        self.assertGreater(product.score, collection.score)
        self.assertEqual(product.decision, "strong")

    def test_approved_secondary_source_remains_eligible(self) -> None:
        result = rank_candidate(
            url="https://docs.example/items/576512",
            title="Philips 576512 specification",
            snippet="65W LED BR40",
            expected_mpn="576512",
            expected_identities=("Philips",),
            approved_domain=True,
            source_role="secondary",
            exact_mpn_in_result=True,
        )

        self.assertIn(result.decision, {"strong", "plausible"})
        self.assertTrue(any("secondary" in reason for reason in result.reasons))

    def test_plausible_candidate_is_not_an_automatic_ready_decision(self) -> None:
        result = rank_candidate(
            url="https://unknown.example/576512",
            title="576512",
            snippet="Part listing",
            expected_mpn="576512",
            approved_domain=False,
            exact_mpn_in_result=True,
        )

        self.assertEqual(result.decision, "plausible")
        self.assertNotEqual(result.decision, "strong")

    def test_plausible_selection_creates_review_without_blocking_verified_source(self) -> None:
        report = build_review_report(
            pipeline_result=None,
            source_diagnostics=[
                type("SourceDiagnostic", (), {"success": True, "url": "https://source.example"})(),
                type(
                    "RankingDiagnostic",
                    (),
                    {
                        "success": False,
                        "code": "CANDIDATE_PLAUSIBLE",
                        "error": "Candidate requires human review.",
                    },
                )(),
            ],
            reference_resolution=None,
            mapping_diagnostics=[],
            evaluation_comparison=None,
        )

        self.assertEqual(report.status, "needs_review")
        self.assertEqual(report.issues[0].code, "CANDIDATE_PLAUSIBLE")
        self.assertTrue(report.issues[0].affects_delivery)


if __name__ == "__main__":
    unittest.main()
