import threading
import time
import unittest

from src.product_intelligence.catalog_input import CatalogInputRow
from src.product_intelligence.runtime_policy import _runtime_discovery_queries
from src.product_intelligence.search_parallel import search_in_order
from src.product_intelligence.source_discovery import SearchResult


class SearchParallelTests(unittest.TestCase):
    def test_worker_pool_is_bounded(self) -> None:
        state = {"active": 0, "maximum": 0}
        lock = threading.Lock()

        class Search:
            def search(self, query, _max_results):
                with lock:
                    state["active"] += 1
                    state["maximum"] = max(state["maximum"], state["active"])
                time.sleep(0.02)
                with lock:
                    state["active"] -= 1
                return [SearchResult(url=f"https://example.test/{query}")]

        results = search_in_order(Search(), [f"Q{n}" for n in range(8)], 10, concurrency=3)

        self.assertEqual(len(results), 8)
        self.assertLessEqual(state["maximum"], 3)
        self.assertGreater(state["maximum"], 1)

    def test_results_are_returned_in_original_query_order(self) -> None:
        class Search:
            def search(self, query, _max_results):
                time.sleep((5 - int(query)) * 0.01)
                return [SearchResult(url=f"https://example.test/{query}")]

        results = search_in_order(Search(), [str(n) for n in range(5)], 10, concurrency=3)

        self.assertEqual(
            [item.results[0].url for item in results],
            [f"https://example.test/{n}" for n in range(5)],
        )

    def test_one_failed_search_does_not_discard_successful_searches(self) -> None:
        class Search:
            def search(self, query, _max_results):
                if query == "bad":
                    raise TimeoutError("transport failure")
                return [SearchResult(url=f"https://example.test/{query}")]

        results = search_in_order(Search(), ["first", "bad", "last"], 10, concurrency=3)

        self.assertEqual(results[0].results[0].url, "https://example.test/first")
        self.assertIsInstance(results[1].error, TimeoutError)
        self.assertEqual(results[2].results[0].url, "https://example.test/last")

    def test_generated_duplicate_queries_remain_deduplicated(self) -> None:
        row = CatalogInputRow(
            Mfg_Part_Num="ABC-1",
            Part_Desc="Product",
            Part_Manuf="Same",
            E1_Brand="Same",
            Unilog_Brand="-- No Unilog Brand --",
            DIB_Brand="-- No DIB Brand --",
        )

        queries = _runtime_discovery_queries(row)

        self.assertEqual(len(queries), len(set(query.casefold() for query in queries)))

    def test_concurrency_one_preserves_execution_order(self) -> None:
        calls = []

        class Search:
            def search(self, query, _max_results):
                calls.append(query)
                return [SearchResult(url=f"https://example.test/{query}")]

        search_in_order(Search(), ["A", "B", "C"], 10, concurrency=1)

        self.assertEqual(calls, ["A", "B", "C"])


if __name__ == "__main__":
    unittest.main()
