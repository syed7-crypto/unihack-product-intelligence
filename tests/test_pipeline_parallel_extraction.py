import threading
import time
import unittest
import inspect
from unittest.mock import patch

from src.product_intelligence.attribute_extraction import AttributeExtractionError
from src.product_intelligence.extraction import extract_file
from src.product_intelligence.pipeline import _extract_attributes_in_order
from src.product_intelligence.pipeline import run_pipeline


SAMPLE = "samples/industrial_valve.txt"


class PipelineParallelExtractionTests(unittest.TestCase):
    def test_public_pipeline_default_is_sequential(self) -> None:
        self.assertEqual(
            inspect.signature(run_pipeline)
            .parameters["attribute_extraction_concurrency"].default,
            1,
        )

    def sources(self):
        first = extract_file(SAMPLE)
        second = extract_file("samples/industrial_valve.pdf")
        third = extract_file("samples/industrial_valve.json")
        return [first, second, third]

    def test_extraction_workers_are_bounded_at_two_and_ordered(self) -> None:
        sources = self.sources()
        state = {"active": 0, "maximum": 0}
        lock = threading.Lock()

        def fake_extract(source, _product, _client):
            with lock:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            time.sleep(0.02)
            with lock:
                state["active"] -= 1
            return source.source_name

        with patch("src.product_intelligence.pipeline.extract_attribute_values", fake_extract):
            outcomes = _extract_attributes_in_order(
                sources, object(), object(), runtime_timing=None, concurrency=2
            )

        self.assertLessEqual(state["maximum"], 2)
        self.assertEqual(outcomes, [source.source_name for source in sources])

    def test_failed_request_does_not_discard_other_outcomes(self) -> None:
        sources = self.sources()

        def fake_extract(source, _product, _client):
            if source.source_name == sources[1].source_name:
                raise AttributeExtractionError(
                    "safe failure", "ATTRIBUTE_RESPONSE_INVALID"
                )
            return source.source_name

        with patch("src.product_intelligence.pipeline.extract_attribute_values", fake_extract):
            outcomes = _extract_attributes_in_order(
                sources, object(), object(), runtime_timing=None, concurrency=2
            )

        self.assertEqual(outcomes[0], sources[0].source_name)
        self.assertIsInstance(outcomes[1], AttributeExtractionError)
        self.assertEqual(outcomes[2], sources[2].source_name)

    def test_concurrency_one_preserves_sequential_call_order(self) -> None:
        sources = self.sources()
        calls = []

        def fake_extract(source, _product, _client):
            calls.append(source.source_name)
            return source.source_name

        with patch("src.product_intelligence.pipeline.extract_attribute_values", fake_extract):
            outcomes = _extract_attributes_in_order(
                sources, object(), object(), runtime_timing=None, concurrency=1
            )

        self.assertEqual(calls, [source.source_name for source in sources])
        self.assertEqual(outcomes, calls)


if __name__ == "__main__":
    unittest.main()
