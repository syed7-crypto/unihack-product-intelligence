"""Tests for product identification and dynamic attribute definitions."""

import json
import unittest

from src.product_intelligence.extraction import NormalizedSource, SourceLocation
from src.product_intelligence.product_identification import (
    ProductIdentificationError,
    ProductIdentificationResult,
    identify_product,
)


class FakeGeminiClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompt = ""
        self.response_schema = None

    def generate_structured_json(self, prompt: str, response_schema: type) -> str:
        self.prompt = prompt
        self.response_schema = response_schema
        return self.response


def source_with_text(text: str) -> NormalizedSource:
    return NormalizedSource(
        source_id="source-test",
        source_type="txt",
        source_name="test.txt",
        extracted_text=text,
        locations=(SourceLocation("document"),),
    )


class ProductIdentificationTests(unittest.TestCase):
    def test_industrial_product_schema(self) -> None:
        response = {
            "product_type": "Industrial Valve",
            "product_category": "Industrial valve",
            "attributes": [
                {"name": "valve_type", "label": "Valve Type"},
                {"name": "material", "label": "Body Material"},
                {"name": "pressure_rating", "label": "Pressure Rating", "data_type": "number", "unit": "psi"},
                {"name": "connection_type", "label": "Connection Type"},
                {"name": "temperature_range", "label": "Temperature Range"},
            ],
        }
        fake_client = FakeGeminiClient(json.dumps(response))

        result = identify_product(
            source_with_text("Industrial valve with stainless steel body and 150 PSI rating."),
            fake_client,
        )

        self.assertIsInstance(result, ProductIdentificationResult)
        self.assertEqual(result.product_type, "Industrial Valve")
        self.assertIn("pressure_rating", {item.name for item in result.attributes})
        self.assertIn("Do not extract actual attribute values", fake_client.prompt)
        self.assertIs(fake_client.response_schema, ProductIdentificationResult)

    def test_different_product_category(self) -> None:
        response = {
            "product_type": "SSD",
            "product_category": "Storage device",
            "attributes": [
                {"name": "capacity", "label": "Capacity", "data_type": "number", "unit": "TB"},
                {"name": "interface", "label": "Interface"},
                {"name": "form_factor", "label": "Form Factor"},
                {"name": "read_speed", "label": "Read Speed", "data_type": "number", "unit": "MB/s"},
                {"name": "write_speed", "label": "Write Speed", "data_type": "number", "unit": "MB/s"},
            ],
        }

        result = identify_product(
            source_with_text("2TB NVMe PCIe Gen4 solid state drive."),
            FakeGeminiClient(json.dumps(response)),
        )

        self.assertEqual(result.product_type, "SSD")
        self.assertIn("interface", {item.name for item in result.attributes})

    def test_malformed_ai_output_is_reported(self) -> None:
        with self.assertRaises(ProductIdentificationError):
            identify_product(source_with_text("Industrial valve"), FakeGeminiClient("not json"))

    def test_required_fields_missing_are_reported(self) -> None:
        response = {
            "product_type": "Industrial Valve",
            "attributes": [{"name": "material", "label": "Body Material"}],
        }

        with self.assertRaises(ProductIdentificationError):
            identify_product(source_with_text("Industrial valve"), FakeGeminiClient(json.dumps(response)))


if __name__ == "__main__":
    unittest.main()
