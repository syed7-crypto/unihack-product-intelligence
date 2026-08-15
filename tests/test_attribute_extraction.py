"""Tests for source-backed dynamic attribute value extraction."""

import json
import unittest

from src.product_intelligence.attribute_extraction import (
    AttributeExtractionError,
    AttributeExtractionResult,
    extract_attribute_values,
)
from src.product_intelligence.extraction import NormalizedSource, SourceLocation
from src.product_intelligence.product_identification import ProductIdentificationResult


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
        source_name="valve.txt",
        extracted_text=text,
        locations=(SourceLocation("document"),),
    )


def valve_schema() -> ProductIdentificationResult:
    return ProductIdentificationResult.model_validate(
        {
            "product_type": "Industrial Valve",
            "product_category": "Industrial valve",
            "attributes": [
                {"name": "material", "label": "Material"},
                {"name": "pressure_rating", "label": "Pressure Rating"},
                {"name": "connection_type", "label": "Connection Type"},
                {"name": "valve_type", "label": "Valve Type"},
            ],
        }
    )


def found(name: str, value: str, quote: str) -> dict:
    return {
        "name": name,
        "value": value,
        "status": "found",
        "evidence": {
            "source_id": "source-test",
            "source_name": "valve.txt",
            "location": "document",
            "quote": quote,
        },
    }


class AttributeExtractionTests(unittest.TestCase):
    def test_values_are_extracted_from_an_industrial_source(self) -> None:
        source = source_with_text(
            "Industrial valve with stainless steel body, 150 PSI pressure rating "
            "and flanged connection."
        )
        response = {
            "attributes": [
                found("material", "stainless steel", "stainless steel body"),
                found("pressure_rating", "150 PSI", "150 PSI pressure rating"),
                found("connection_type", "flanged", "flanged connection"),
                {
                    "name": "valve_type",
                    "value": None,
                    "status": "not_found",
                    "evidence": None,
                },
            ]
        }

        result = extract_attribute_values(source, valve_schema(), FakeGeminiClient(json.dumps(response)))

        values = {attribute.name: attribute.value for attribute in result.attributes}
        self.assertEqual(values["pressure_rating"], "150 PSI")
        self.assertEqual(values["connection_type"], "flanged")
        self.assertIsNone(values["valve_type"])

    def test_absent_attribute_is_not_found(self) -> None:
        source = source_with_text("Industrial valve with stainless steel body.")
        response = {
            "attributes": [
                found("material", "stainless steel", "stainless steel body"),
                {"name": "pressure_rating", "value": None, "status": "not_found"},
                {"name": "connection_type", "value": None, "status": "not_found"},
                {"name": "valve_type", "value": None, "status": "not_found"},
            ]
        }

        result = extract_attribute_values(source, valve_schema(), FakeGeminiClient(json.dumps(response)))

        pressure = next(item for item in result.attributes if item.name == "pressure_rating")
        self.assertEqual(pressure.status, "not_found")
        self.assertIsNone(pressure.value)
        self.assertIsNone(pressure.evidence)

    def test_found_value_keeps_evidence(self) -> None:
        source = source_with_text("Maximum working pressure: 150 PSI")
        schema = ProductIdentificationResult.model_validate(
            {
                "product_type": "Valve",
                "product_category": "Industrial valve",
                "attributes": [{"name": "pressure_rating", "label": "Pressure Rating"}],
            }
        )
        response = {"attributes": [found("pressure_rating", "150 PSI", "Maximum working pressure: 150 PSI")]}

        result = extract_attribute_values(source, schema, FakeGeminiClient(json.dumps(response)))

        evidence = result.attributes[0].evidence
        assert evidence is not None
        self.assertEqual(evidence.source_id, "source-test")
        self.assertEqual(evidence.source_name, "valve.txt")
        self.assertIn("150 PSI", evidence.quote)

    def test_malformed_gemini_output_is_reported(self) -> None:
        with self.assertRaises(AttributeExtractionError):
            extract_attribute_values(source_with_text("Industrial valve"), valve_schema(), FakeGeminiClient("not json"))

    def test_found_value_without_supporting_evidence_is_rejected(self) -> None:
        source = source_with_text("Industrial valve with stainless steel body.")
        response = {
            "attributes": [
                {"name": "material", "value": "stainless steel", "status": "found", "evidence": None},
                {"name": "pressure_rating", "value": None, "status": "not_found"},
                {"name": "connection_type", "value": None, "status": "not_found"},
                {"name": "valve_type", "value": None, "status": "not_found"},
            ]
        }

        with self.assertRaises(AttributeExtractionError):
            extract_attribute_values(source, valve_schema(), FakeGeminiClient(json.dumps(response)))

    def test_evidence_quote_not_in_source_is_rejected(self) -> None:
        source = source_with_text("Industrial valve with stainless steel body.")
        response = {
            "attributes": [
                found("material", "carbon steel", "carbon steel body"),
                {"name": "pressure_rating", "value": None, "status": "not_found"},
                {"name": "connection_type", "value": None, "status": "not_found"},
                {"name": "valve_type", "value": None, "status": "not_found"},
            ]
        }

        with self.assertRaises(AttributeExtractionError):
            extract_attribute_values(source, valve_schema(), FakeGeminiClient(json.dumps(response)))


if __name__ == "__main__":
    unittest.main()
