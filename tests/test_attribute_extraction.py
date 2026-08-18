"""Tests for source-backed dynamic attribute value extraction."""

import json
import unittest

from src.product_intelligence.attribute_extraction import (
    AttributeExtractionError,
    AttributeExtractionResult,
    GeminiAttributeExtractionResult,
    extract_attribute_values,
    normalize_location_label,
)
from pydantic import ValidationError
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


def source_with_type(text: str, source_type: str, location: str = "document") -> NormalizedSource:
    return NormalizedSource(
        source_id="source-test",
        source_type=source_type,
        source_name=f"valve.{source_type}",
        extracted_text=text,
        locations=(SourceLocation(location, 1 if source_type == "pdf" else None),),
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


def found_for_source(
    source: NormalizedSource,
    name: str,
    value: str,
    quote: str,
    location: str,
) -> dict:
    return {
        "name": name,
        "value": value,
        "status": "found",
        "evidence": {
            "source_id": source.source_id,
            "source_name": source.source_name,
            "location": location,
            "quote": quote,
        },
    }


class AttributeExtractionTests(unittest.TestCase):
    def test_gemini_found_contract_requires_value_and_evidence(self) -> None:
        valid = {
            "name": "material",
            "status": "found",
            "value": "Stainless Steel",
            "evidence": {
                "source_id": "source-test",
                "source_name": "valve.txt",
                "location": "document",
                "quote": "Material: Stainless Steel",
            },
        }
        parsed = AttributeExtractionResult.model_validate({"attributes": [valid]})
        self.assertEqual(parsed.attributes[0].value, "Stainless Steel")

        for invalid in (
            {**valid, "value": None},
            {key: value for key, value in valid.items() if key != "value"},
            {**valid, "evidence": None},
            {key: value for key, value in valid.items() if key != "evidence"},
        ):
            with self.assertRaises(ValidationError):
                AttributeExtractionResult.model_validate({"attributes": [invalid]})

    def test_gemini_not_found_contract_requires_null_value_and_evidence(self) -> None:
        valid = {"name": "temperature", "status": "not_found", "value": None, "evidence": None}
        parsed = AttributeExtractionResult.model_validate({"attributes": [valid]})
        self.assertIsNone(parsed.attributes[0].value)

        with self.assertRaises(ValidationError):
            AttributeExtractionResult.model_validate({"attributes": [{**valid, "value": "20 C"}]})
        with self.assertRaises(ValidationError):
            AttributeExtractionResult.model_validate({"attributes": [{**valid, "evidence": {"source_id": "x", "source_name": "x", "quote": "20 C"}}]})

    def test_gemini_schema_is_flat_and_has_no_union_keywords(self) -> None:
        schema = GeminiAttributeExtractionResult.model_json_schema()
        schema_text = json.dumps(schema)
        self.assertNotIn("discriminator", schema_text)
        self.assertNotIn("oneOf", schema_text)
        required = schema["$defs"]["GeminiAttributeResponse"]["required"]
        for key in ("name", "status", "value", "evidence"):
            self.assertIn(key, required)

    def test_extraction_uses_flat_gemini_schema(self) -> None:
        response = {"attributes": [{"name": "material", "value": None, "status": "not_found", "evidence": None}]}
        source = source_with_text("Industrial valve")
        schema = ProductIdentificationResult.model_validate(
            {"product_type": "Valve", "product_category": "Valve", "attributes": [{"name": "material", "label": "Material"}]}
        )
        client = FakeGeminiClient(json.dumps(response))
        extract_attribute_values(source, schema, client)
        self.assertIs(client.response_schema, GeminiAttributeExtractionResult)

    def test_value_not_supported_by_valid_quote_is_rejected(self) -> None:
        source = source_with_text("Material: Stainless Steel")
        response = {
            "attributes": [
                found("material", "Carbon Steel", "Material: Stainless Steel"),
                {"name": "pressure_rating", "value": None, "status": "not_found", "evidence": None},
                {"name": "connection_type", "value": None, "status": "not_found", "evidence": None},
                {"name": "valve_type", "value": None, "status": "not_found", "evidence": None},
            ]
        }

        result = extract_attribute_values(source, valve_schema(), FakeGeminiClient(json.dumps(response)))
        self.assertEqual(result.attributes[0].status, "not_found")
        self.assertEqual(result.rejected_attributes[0].code, "EVIDENCE_VALUE_NOT_IN_QUOTE")

    def test_value_not_present_in_source_is_rejected(self) -> None:
        source = source_with_text("Material: Stainless Steel. Pressure rating: 150 PSI")
        response = {
            "attributes": [
                found("material", "Carbon Steel", "Pressure rating: 150 PSI"),
                {"name": "pressure_rating", "value": None, "status": "not_found", "evidence": None},
                {"name": "connection_type", "value": None, "status": "not_found", "evidence": None},
                {"name": "valve_type", "value": None, "status": "not_found", "evidence": None},
            ]
        }

        result = extract_attribute_values(source, valve_schema(), FakeGeminiClient(json.dumps(response)))
        self.assertEqual(result.attributes[0].status, "not_found")
        self.assertEqual(result.rejected_attributes[0].code, "EVIDENCE_VALUE_NOT_IN_QUOTE")

    def test_case_and_whitespace_variation_between_value_and_quote_is_accepted(self) -> None:
        source = source_with_text("Material: Stainless   Steel")
        response = {
            "attributes": [
                found("material", " stainless steel ", "Material: Stainless   Steel"),
                {"name": "pressure_rating", "value": None, "status": "not_found", "evidence": None},
                {"name": "connection_type", "value": None, "status": "not_found", "evidence": None},
                {"name": "valve_type", "value": None, "status": "not_found", "evidence": None},
            ]
        }

        result = extract_attribute_values(source, valve_schema(), FakeGeminiClient(json.dumps(response)))

        self.assertEqual(result.attributes[0].value, " stainless steel ")

    def test_numeric_value_unit_spacing_variation_is_accepted(self) -> None:
        source = source_with_text("Voltage: 120 V")
        schema = ProductIdentificationResult.model_validate(
            {
                "product_type": "Valve",
                "product_category": "Valve",
                "attributes": [{"name": "voltage", "label": "Voltage"}],
            }
        )
        response = {
            "attributes": [
                found("voltage", "120V", "Voltage: 120 V"),
            ]
        }

        result = extract_attribute_values(source, schema, FakeGeminiClient(json.dumps(response)))

        self.assertEqual(result.attributes[0].status, "found")
        self.assertEqual(result.attributes[0].value, "120V")

    def test_different_numeric_value_is_still_rejected(self) -> None:
        source = source_with_text("Voltage: 120 V")
        schema = ProductIdentificationResult.model_validate(
            {
                "product_type": "Valve",
                "product_category": "Valve",
                "attributes": [{"name": "voltage", "label": "Voltage"}],
            }
        )
        response = {"attributes": [found("voltage", "240V", "Voltage: 120 V")]}

        result = extract_attribute_values(source, schema, FakeGeminiClient(json.dumps(response)))

        self.assertEqual(result.attributes[0].status, "not_found")
        self.assertEqual(result.rejected_attributes[0].code, "EVIDENCE_VALUE_NOT_IN_QUOTE")

    def test_invalid_txt_location_is_rejected(self) -> None:
        source = source_with_type("Material: Stainless Steel", "txt")
        schema = ProductIdentificationResult.model_validate(
            {"product_type": "Valve", "product_category": "Valve", "attributes": [{"name": "material", "label": "Material"}]}
        )
        response = {"attributes": [found_for_source(source, "material", "Stainless Steel", "Material: Stainless Steel", "page 99")]}

        result = extract_attribute_values(source, schema, FakeGeminiClient(json.dumps(response)))
        self.assertEqual(result.attributes[0].status, "not_found")
        self.assertEqual(result.rejected_attributes[0].code, "EVIDENCE_LOCATION_INVALID")

    def test_invalid_json_location_is_rejected(self) -> None:
        source = source_with_type('{"material": "Stainless Steel"}', "json")
        schema = ProductIdentificationResult.model_validate(
            {"product_type": "Valve", "product_category": "Valve", "attributes": [{"name": "material", "label": "Material"}]}
        )
        response = {"attributes": [found_for_source(source, "material", "Stainless Steel", '"material": "Stainless Steel"', "page 99")]}

        result = extract_attribute_values(source, schema, FakeGeminiClient(json.dumps(response)))
        self.assertEqual(result.attributes[0].status, "not_found")
        self.assertEqual(result.rejected_attributes[0].code, "EVIDENCE_LOCATION_INVALID")

    def test_valid_pdf_location_is_accepted(self) -> None:
        source = source_with_type("Material: Stainless Steel", "pdf", "page 1")
        schema = ProductIdentificationResult.model_validate(
            {"product_type": "Valve", "product_category": "Valve", "attributes": [{"name": "material", "label": "Material"}]}
        )
        response = {"attributes": [found_for_source(source, "material", "Stainless Steel", "Material: Stainless Steel", "page 1")]}

        result = extract_attribute_values(source, schema, FakeGeminiClient(json.dumps(response)))

        self.assertEqual(result.attributes[0].status, "found")

    def test_invalid_pdf_location_is_rejected(self) -> None:
        source = source_with_type("Material: Stainless Steel", "pdf", "page 1")
        schema = ProductIdentificationResult.model_validate(
            {"product_type": "Valve", "product_category": "Valve", "attributes": [{"name": "material", "label": "Material"}]}
        )
        response = {"attributes": [found_for_source(source, "material", "Stainless Steel", "Material: Stainless Steel", "page 2")]}

        result = extract_attribute_values(source, schema, FakeGeminiClient(json.dumps(response)))
        self.assertEqual(result.attributes[0].status, "not_found")
        self.assertEqual(result.rejected_attributes[0].code, "EVIDENCE_LOCATION_INVALID")

    def test_webpage_document_location_is_valid(self) -> None:
        source = source_with_type("Product details: Stainless Steel", "web")
        schema = ProductIdentificationResult.model_validate(
            {"product_type": "Valve", "product_category": "Valve", "attributes": [{"name": "material", "label": "Material"}]}
        )
        response = {"attributes": [found_for_source(source, "material", "Stainless Steel", "Product details: Stainless Steel", "document")]}

        client = FakeGeminiClient(json.dumps(response))
        result = extract_attribute_values(source, schema, client)

        self.assertEqual(result.attributes[0].evidence.location, "document")
        self.assertIn('For webpage sources, use "document"', client.prompt)
        self.assertIn("copy that heading label exactly as provided", client.prompt)

    def test_webpage_heading_formatting_resolves_to_canonical_label(self) -> None:
        source = source_with_type("Stainless Steel body", "web", "Product Details")
        schema = ProductIdentificationResult.model_validate(
            {"product_type": "Valve", "product_category": "Valve", "attributes": [{"name": "material", "label": "Material"}]}
        )
        response = {"attributes": [found_for_source(source, "material", "Stainless Steel", "Stainless Steel body", " PRODUCT   DETAILS ")]}

        result = extract_attribute_values(source, schema, FakeGeminiClient(json.dumps(response)))

        self.assertEqual(result.attributes[0].evidence.location, "Product Details")
        self.assertEqual(normalize_location_label(" PRODUCT   DETAILS "), "product details")

    def test_webpage_unknown_or_inferred_heading_falls_back_to_document(self) -> None:
        source = source_with_type("Stainless Steel body", "web", "Product Details")
        schema = ProductIdentificationResult.model_validate(
            {"product_type": "Valve", "product_category": "Valve", "attributes": [{"name": "material", "label": "Material"}]}
        )

        for location in ("Unknown Section", "Product Details > Specifications"):
            response = {"attributes": [found_for_source(source, "material", "Stainless Steel", "Stainless Steel body", location)]}
            result = extract_attribute_values(source, schema, FakeGeminiClient(json.dumps(response)))
            self.assertEqual(result.attributes[0].status, "found")
            self.assertEqual(result.attributes[0].evidence.location, "document")

    def test_webpage_missing_location_falls_back_to_document(self) -> None:
        source = source_with_type("Stainless Steel body", "web", "Product Details")
        schema = ProductIdentificationResult.model_validate(
            {"product_type": "Valve", "product_category": "Valve", "attributes": [{"name": "material", "label": "Material"}]}
        )
        response = {
            "attributes": [{
                "name": "material",
                "value": "Stainless Steel",
                "status": "found",
                "evidence": {
                    "source_id": source.source_id,
                    "source_name": source.source_name,
                    "location": None,
                    "quote": "Stainless Steel body",
                },
            }]
        }

        result = extract_attribute_values(source, schema, FakeGeminiClient(json.dumps(response)))

        self.assertEqual(result.attributes[0].evidence.location, "document")

    def test_webpage_location_fix_does_not_bypass_quote_validation(self) -> None:
        source = source_with_type("Stainless Steel body", "web", "Product Details")
        schema = ProductIdentificationResult.model_validate(
            {"product_type": "Valve", "product_category": "Valve", "attributes": [{"name": "material", "label": "Material"}]}
        )
        response = {"attributes": [found_for_source(source, "material", "Carbon Steel", "Stainless Steel body", " product details ")]}

        result = extract_attribute_values(source, schema, FakeGeminiClient(json.dumps(response)))

        self.assertEqual(result.attributes[0].status, "not_found")
        self.assertEqual(result.rejected_attributes[0].code, "EVIDENCE_VALUE_NOT_IN_QUOTE")

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
                {"name": "pressure_rating", "value": None, "status": "not_found", "evidence": None},
                {"name": "connection_type", "value": None, "status": "not_found", "evidence": None},
                {"name": "valve_type", "value": None, "status": "not_found", "evidence": None},
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
                {"name": "pressure_rating", "value": None, "status": "not_found", "evidence": None},
                {"name": "connection_type", "value": None, "status": "not_found", "evidence": None},
                {"name": "valve_type", "value": None, "status": "not_found", "evidence": None},
            ]
        }

        with self.assertRaises(AttributeExtractionError):
            extract_attribute_values(source, valve_schema(), FakeGeminiClient(json.dumps(response)))

    def test_invalid_attribute_does_not_discard_valid_attribute(self) -> None:
        source = source_with_text("Material: Stainless Steel. Pressure: 150 PSI")
        schema = ProductIdentificationResult.model_validate(
            {
                "product_type": "Valve",
                "product_category": "Valve",
                "attributes": [
                    {"name": "material", "label": "Material"},
                    {"name": "pressure_rating", "label": "Pressure"},
                ],
            }
        )
        response = {
            "attributes": [
                found("material", "Stainless Steel", "Material: Stainless Steel"),
                found("pressure_rating", "200 PSI", "Pressure: 150 PSI"),
            ]
        }

        result = extract_attribute_values(source, schema, FakeGeminiClient(json.dumps(response)))

        self.assertEqual(result.attributes[0].status, "found")
        self.assertEqual(result.attributes[0].value, "Stainless Steel")
        self.assertEqual(result.attributes[1].status, "not_found")
        self.assertEqual(result.rejected_attributes[0].name, "pressure_rating")
        self.assertIsNone(result.attributes[1].evidence)

    def test_evidence_quote_not_in_source_is_rejected(self) -> None:
        source = source_with_text("Industrial valve with stainless steel body.")
        response = {
            "attributes": [
                found("material", "carbon steel", "carbon steel body"),
                {"name": "pressure_rating", "value": None, "status": "not_found", "evidence": None},
                {"name": "connection_type", "value": None, "status": "not_found", "evidence": None},
                {"name": "valve_type", "value": None, "status": "not_found", "evidence": None},
            ]
        }

        result = extract_attribute_values(source, valve_schema(), FakeGeminiClient(json.dumps(response)))
        self.assertEqual(result.attributes[0].status, "not_found")
        self.assertEqual(result.rejected_attributes[0].code, "EVIDENCE_QUOTE_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
