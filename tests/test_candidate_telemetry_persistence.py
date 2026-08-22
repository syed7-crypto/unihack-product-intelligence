import csv
import io
import unittest

from src.product_intelligence.catalog_input import CatalogInputRow, INPUT_COLUMNS
from src.product_intelligence.catalogue_batch import (
    BatchCandidateTelemetry,
    BatchResult,
    run_catalogue_batch,
)
from src.product_intelligence.catalogue_enrichment import CatalogueEnrichmentResult
from src.product_intelligence.candidate_ranking import CandidateRanking
from src.product_intelligence.delivery_schema import DeliverySchema
from src.product_intelligence.manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    RetrievedPayload,
)
from src.product_intelligence.review import ReviewReport
from src.product_intelligence.runtime_policy import (
    CandidateTelemetry,
    RuntimeDomainCandidate,
)
from src.product_intelligence.source_discovery import InMemorySourceSearchProvider
from src.product_intelligence.ui import (
    build_candidate_telemetry_rows,
    build_result_rows,
    build_review_rows,
    candidate_telemetry_csv_bytes,
)


def row() -> CatalogInputRow:
    return CatalogInputRow(
        Mfg_Part_Num="MPN-1",
        Part_Desc="Acme Widget",
        E1_Brand="-- Unbranded --",
        Unilog_Brand="-- No Unilog Brand --",
        DIB_Brand="-- No DIB Brand --",
        Part_Manuf="Acme",
    )


def schema() -> DeliverySchema:
    return DeliverySchema((*INPUT_COLUMNS, "MFR URL"))


def ranking(decision: str = "strong") -> CandidateRanking:
    return CandidateRanking(
        decision=decision,
        score=8,
        page_type="product",
        visible_mpn_match=True,
        identity_match=True,
    )


def telemetry(**overrides) -> CandidateTelemetry:
    values = {
        "url": "https://approved.example/products/mpn-1",
        "domain": "approved.example",
        "ranking": ranking(),
        "fetched": True,
        "http_status": 200,
        "content_type": "text/html",
        "exact_mpn_verified": True,
        "identity_value": "Acme",
        "identity_kind": "manufacturer",
        "identity_result": "verified",
    }
    values.update(overrides)
    return CandidateTelemetry(**values)


class CandidateTelemetryPersistenceTests(unittest.TestCase):
    def test_batch_result_retains_runtime_telemetry(self) -> None:
        url = "https://approved.example/products/mpn-1"

        def fetch(_url: str, _timeout: float) -> RetrievedPayload:
            return RetrievedPayload(
                200,
                {"content-type": "text/html"},
                b"<title>Acme Widget MPN-1</title><p>Acme MPN-1</p>",
            )

        result = run_catalogue_batch(
            [row()],
            schema(),
            discovery_enabled=True,
            runtime_policy_resolution_enabled=True,
            runtime_candidate_domain_provider=lambda _row: [
                RuntimeDomainCandidate(domain="approved.example", discovery_url=url)
            ],
            search_provider=InMemorySourceSearchProvider({}),
            provider=ManufacturerEnrichmentProvider(
                approved_domains={"approved.example"}, fetcher=fetch
            ),
        )

        self.assertEqual(len(result.candidate_telemetry), 1)
        self.assertEqual(result.candidate_telemetry[0].mfg_part_num, "MPN-1")
        self.assertEqual(result.candidate_telemetry[0].telemetry.url, url)
        self.assertEqual(
            result.candidate_telemetry[0].telemetry.query,
            'site:approved.example "MPN-1"',
        )
        self.assertEqual(result.row_results[0].candidate_telemetry[0].url, url)

    def test_successful_and_rejected_candidates_are_serialized(self) -> None:
        result = BatchResult(
            total_rows=2,
            processed_rows=2,
            ready_rows=0,
            needs_review_rows=1,
            blocked_rows=1,
            failed_rows=0,
            candidate_telemetry=[
                BatchCandidateTelemetry(mfg_part_num="GOOD", telemetry=telemetry()),
                BatchCandidateTelemetry(
                    mfg_part_num="BAD",
                    telemetry=telemetry(
                        url="https://retailer.example/mpn-1",
                        domain="retailer.example",
                        ranking=ranking("bad"),
                        fetched=False,
                        http_status=None,
                        content_type=None,
                        exact_mpn_verified=None,
                        identity_value=None,
                        identity_kind=None,
                        identity_result="rejected",
                        rejection_code="RETAILER_DOMAIN_REJECTED",
                    ),
                ),
            ],
        )

        rows = build_candidate_telemetry_rows(result)
        self.assertEqual(rows[0]["decision"], "strong")
        self.assertEqual(rows[0]["query"], "")
        self.assertEqual(rows[0]["exact_mpn"], True)
        self.assertEqual(rows[1]["rejection_code"], "RETAILER_DOMAIN_REJECTED")
        self.assertEqual(rows[1]["fetched"], False)

        parsed = list(csv.DictReader(io.StringIO(candidate_telemetry_csv_bytes(result).decode())))
        self.assertEqual([item["MPN"] for item in parsed], ["GOOD", "BAD"])

    def test_attempt_limit_candidate_is_serialized(self) -> None:
        item = telemetry(
            url="https://approved.example/products/second",
            fetched=False,
            http_status=None,
            content_type=None,
            exact_mpn_verified=None,
            identity_value=None,
            identity_kind=None,
            identity_result="not_fetched",
            rejection_code="ATTEMPT_LIMIT_REACHED",
        )
        result = BatchResult(
            total_rows=1,
            processed_rows=1,
            ready_rows=0,
            needs_review_rows=1,
            blocked_rows=0,
            failed_rows=0,
            candidate_telemetry=[
                BatchCandidateTelemetry(mfg_part_num="MPN-1", telemetry=item)
            ],
        )
        row_data = build_candidate_telemetry_rows(result)[0]
        self.assertEqual(row_data["rejection_code"], "ATTEMPT_LIMIT_REACHED")
        self.assertEqual(row_data["identity_result"], "not_fetched")

    def test_missing_optional_telemetry_fields_serialize_safely(self) -> None:
        result = BatchResult(
            total_rows=1,
            processed_rows=1,
            ready_rows=0,
            needs_review_rows=1,
            blocked_rows=0,
            failed_rows=0,
            candidate_telemetry=[
                BatchCandidateTelemetry(
                    mfg_part_num="MPN-1",
                    telemetry=CandidateTelemetry(),
                )
            ],
        )
        row_data = build_candidate_telemetry_rows(result)[0]
        self.assertEqual(row_data["candidate_url"], "")
        self.assertEqual(row_data["http_status"], "")
        self.assertEqual(row_data["score"], "")
        self.assertTrue(candidate_telemetry_csv_bytes(result).startswith(b"MPN,"))

    def test_existing_result_and_review_shapes_are_unchanged(self) -> None:
        enrichment = CatalogueEnrichmentResult(
            catalogue_row=row(),
            pipeline_result=None,
            delivery_row={},
            review=ReviewReport(status="needs_review"),
        )
        result = BatchResult(
            total_rows=1,
            processed_rows=1,
            ready_rows=0,
            needs_review_rows=1,
            blocked_rows=0,
            failed_rows=0,
            row_results=[enrichment],
        )
        self.assertEqual(
            set(build_result_rows(result)[0]),
            {"MPN", "Manufacturer", "Verified source", "Verified sources", "Accepted attributes", "Status"},
        )
        self.assertEqual(build_review_rows(result), [])


if __name__ == "__main__":
    unittest.main()
