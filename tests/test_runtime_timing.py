import unittest

from src.product_intelligence.catalogue_batch import _TimedSearchProvider
from src.product_intelligence.manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    RetrievedPayload,
)
from src.product_intelligence.runtime_timing import RuntimeTimingAccumulator
from src.product_intelligence.source_discovery import SearchResult
from src.product_intelligence.ui import (
    runtime_diagnostics_csv_bytes,
    search_diagnostics_csv_bytes,
)
from src.product_intelligence.catalogue_batch import BatchResult


class RuntimeTimingTests(unittest.TestCase):
    def test_accumulator_records_durations_and_calls_without_payloads(self) -> None:
        ticks = iter([10.0, 10.25, 11.0, 11.5])
        timing = RuntimeTimingAccumulator(clock=lambda: next(ticks))

        with timing.measure("product_identification_duration_seconds", "product_identification_calls"):
            pass
        with timing.measure("attribute_extraction_duration_seconds", "attribute_extraction_calls"):
            pass

        summary = timing.snapshot()
        self.assertEqual(summary.product_identification_calls, 1)
        self.assertEqual(summary.attribute_extraction_calls, 1)
        self.assertAlmostEqual(summary.product_identification_duration_seconds, 0.25)
        self.assertAlmostEqual(summary.attribute_extraction_duration_seconds, 0.5)

    def test_search_timing_distinguishes_domain_queries(self) -> None:
        class Search:
            def search(self, query, max_results):
                return [SearchResult(url="https://example.com/item")]

        ticks = iter([1.0, 1.1, 2.0, 2.4])
        timing = RuntimeTimingAccumulator(clock=lambda: next(ticks))
        provider = _TimedSearchProvider(Search(), timing).for_mpn("ABC")
        provider.search("ABC", 10)
        provider.search('site:example.com "ABC"', 3)

        summary = timing.snapshot()
        self.assertEqual(summary.serper_search_calls, 2)
        self.assertEqual(summary.domain_search_calls, 1)
        self.assertAlmostEqual(summary.serper_search_duration_seconds, 0.5)
        self.assertAlmostEqual(summary.domain_search_duration_seconds, 0.4)

    def test_search_failure_records_safe_category_and_zero_results(self) -> None:
        class Search:
            def search(self, query, max_results):
                from src.product_intelligence.source_discovery import SearchProviderError
                raise SearchProviderError("rate_limited", "HTTP 429 with sensitive details")

        timing = RuntimeTimingAccumulator(clock=lambda: 1.0)
        provider = _TimedSearchProvider(Search(), timing).for_mpn("ABC")
        with self.assertRaises(Exception):
            provider.search("ABC", 10)

        records = timing.search_snapshot()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].mpn, "ABC")
        self.assertEqual(records[0].result_count, 0)
        self.assertEqual(records[0].error_category, "rate_limited")
        self.assertNotIn("sensitive", records[0].model_dump_json())

    def test_source_retrieval_timing_counts_successful_retrieval(self) -> None:
        timing = RuntimeTimingAccumulator(clock=lambda: 5.0)
        provider = ManufacturerEnrichmentProvider(
            approved_domains={"example.com"},
            fetcher=lambda url, timeout: RetrievedPayload(
                200, {"content-type": "text/html"}, b"<h1>ABC-1</h1>"
            ),
            runtime_timing=timing,
        )

        result = provider.retrieve_source("https://example.com/ABC-1", "ABC-1")

        self.assertTrue(result.success)
        self.assertEqual(timing.snapshot().source_retrieval_calls, 1)

    def test_runtime_diagnostics_csv_contains_only_aggregate_metrics(self) -> None:
        batch = BatchResult(
            total_rows=0,
            processed_rows=0,
            ready_rows=0,
            needs_review_rows=0,
            blocked_rows=0,
            failed_rows=0,
        )
        csv_text = runtime_diagnostics_csv_bytes(batch).decode("utf-8")

        self.assertIn("total_batch_duration_seconds", csv_text)
        self.assertIn("serper_search_calls", csv_text)
        self.assertNotIn("prompt", csv_text.casefold())
        self.assertNotIn("api_key", csv_text.casefold())

    def test_search_diagnostics_csv_serializes_bounded_records(self) -> None:
        timing = RuntimeTimingAccumulator(clock=lambda: 1.0)
        timing.record_search(
            mpn="ABC",
            query="ABC product",
            query_kind="initial",
            duration_seconds=1.25,
            result_count=2,
        )
        batch = BatchResult(
            total_rows=1,
            processed_rows=1,
            ready_rows=0,
            needs_review_rows=1,
            blocked_rows=0,
            failed_rows=0,
            search_telemetry=timing.search_snapshot(),
        )
        csv_text = search_diagnostics_csv_bytes(batch).decode("utf-8")

        self.assertIn("ABC,ABC product,initial,1.25,2,", csv_text)


if __name__ == "__main__":
    unittest.main()
