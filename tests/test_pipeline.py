"""End-to-end tests for the product intelligence orchestrator."""

import json
import tempfile
import unittest
from pathlib import Path

from src.product_intelligence.pipeline import (
    ProductIntelligencePipelineError,
    ProductIntelligenceResult,
    run_pipeline,
)
from src.product_intelligence.extraction import extract_file


PROJECT_ROOT = Path(__file__).parents[1]
SAMPLES = PROJECT_ROOT / "samples"


IDENTIFICATION_RESPONSE = {
    "product_type": "Industrial Valve",
    "product_category": "Industrial valve",
    "attributes": [
        {"name": "pressure_rating", "label": "Pressure Rating"},
        {"name": "material", "label": "Material"},
        {"name": "connection_type", "label": "Connection Type"},
        {"name": "valve_type", "label": "Valve Type"},
    ],
}


class SequentialGeminiClient:
    """Return prepared structured responses without calling Gemini."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = [json.dumps(response) for response in responses]
        self.prompts: list[str] = []
        self.schemas: list[type] = []

    def generate_structured_json(self, prompt: str, response_schema: type) -> str:
        self.prompts.append(prompt)
        self.schemas.append(response_schema)
        if not self.responses:
            raise AssertionError("The pipeline made more Gemini calls than expected.")
        return self.responses.pop(0)


def found(
    name: str,
    value: str,
    source,
    quote: str,
    location: str = "document",
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


def missing(name: str) -> dict:
    return {"name": name, "value": None, "status": "not_found", "evidence": None}


def extraction_response(source, *, pressure_value: str = "150 PSI") -> dict:
    is_pdf = source.source_type == "pdf"
    pressure_quote = (
        "Acme Industrial Valve - Pressure Rating: 150 PSI"
        if is_pdf
        else "Pressure rating: 150 PSI"
    )
    location = "page 1" if is_pdf else "document"
    return {
        "attributes": [
            found("pressure_rating", pressure_value, source, pressure_quote, location),
            missing("material"),
            missing("connection_type"),
            missing("valve_type"),
        ]
    }


class PipelineTests(unittest.TestCase):
    def test_successful_pipeline_connects_all_stages(self) -> None:
        txt = extract_file(SAMPLES / "industrial_valve.txt")
        pdf = extract_file(SAMPLES / "industrial_valve.pdf")
        client = SequentialGeminiClient(
            [IDENTIFICATION_RESPONSE, extraction_response(txt), extraction_response(pdf)]
        )

        result = run_pipeline(
            [SAMPLES / "industrial_valve.txt", SAMPLES / "industrial_valve.pdf"],
            client,
        )

        self.assertIsInstance(result, ProductIntelligenceResult)
        self.assertEqual(result.product_identification.product_type, "Industrial Valve")
        self.assertEqual(
            [attribute.name for attribute in result.dynamic_attribute_schema],
            ["pressure_rating", "material", "connection_type", "valve_type"],
        )
        self.assertEqual(len(result.extracted_attributes), 2)
        self.assertEqual(result.validation.attributes[0].status, "consistent")
        self.assertEqual(result.confidence.attributes[0].level, "high")
        self.assertEqual(result.sources[1].locations, ["page 1"])
        self.assertEqual(len(client.schemas), 3)
        self.assertFalse(client.responses)

    def test_multiple_txt_json_pdf_sources_are_retained(self) -> None:
        txt = extract_file(SAMPLES / "industrial_valve.txt")
        json_source = extract_file(SAMPLES / "industrial_valve.json")
        pdf = extract_file(SAMPLES / "industrial_valve.pdf")
        client = SequentialGeminiClient(
            [
                IDENTIFICATION_RESPONSE,
                extraction_response(txt),
                {
                    "attributes": [
                        found("pressure_rating", "150", json_source, '"pressure_rating_psi": 150'),
                        missing("material"),
                        missing("connection_type"),
                        missing("valve_type"),
                    ]
                },
                extraction_response(pdf),
            ]
        )

        result = run_pipeline(
            [
                SAMPLES / "industrial_valve.txt",
                SAMPLES / "industrial_valve.json",
                SAMPLES / "industrial_valve.pdf",
            ],
            client,
        )

        self.assertEqual([source.source_name for source in result.sources], [
            "industrial_valve.txt", "industrial_valve.json", "industrial_valve.pdf"
        ])
        pressure = result.validation.attributes[0]
        self.assertEqual(pressure.status, "conflict")
        self.assertEqual(len(pressure.values), 3)
        self.assertEqual(
            [value.evidence.source_name for value in pressure.values],
            ["industrial_valve.txt", "industrial_valve.json", "industrial_valve.pdf"],
        )

    def test_missing_attribute_flows_to_validation_and_confidence(self) -> None:
        txt = extract_file(SAMPLES / "industrial_valve.txt")
        identification = {
            **IDENTIFICATION_RESPONSE,
            "attributes": [*IDENTIFICATION_RESPONSE["attributes"], {"name": "temperature_range", "label": "Temperature Range"}],
        }
        response = extraction_response(txt)
        response["attributes"].append(missing("temperature_range"))
        client = SequentialGeminiClient([identification, response])

        result = run_pipeline([SAMPLES / "industrial_valve.txt"], client)

        temperature = next(item for item in result.validation.attributes if item.name == "temperature_range")
        temperature_confidence = next(item for item in result.confidence.attributes if item.name == "temperature_range")
        self.assertEqual(temperature.status, "not_found")
        self.assertEqual(temperature_confidence.score, 0.0)

    def test_invalid_attribute_is_reviewed_while_valid_attribute_reaches_validation(self) -> None:
        txt = extract_file(SAMPLES / "industrial_valve.txt")
        response = extraction_response(txt)
        response["attributes"][0] = found(
            "pressure_rating",
            "200 PSI",
            txt,
            "Pressure rating: 150 PSI",
        )
        response["attributes"][1] = found(
            "material",
            "Stainless steel",
            txt,
            "Body material: Stainless steel",
        )
        client = SequentialGeminiClient([IDENTIFICATION_RESPONSE, response])

        result = run_pipeline([SAMPLES / "industrial_valve.txt"], client)

        pressure = next(item for item in result.validation.attributes if item.name == "pressure_rating")
        material = next(item for item in result.validation.attributes if item.name == "material")
        self.assertEqual(pressure.status, "not_found")
        self.assertEqual(material.status, "single_source")
        self.assertEqual(result.extracted_attributes[0].rejected_attributes[0].name, "pressure_rating")

    def test_conflicting_values_are_preserved_end_to_end(self) -> None:
        txt = extract_file(SAMPLES / "industrial_valve.txt")
        with tempfile.TemporaryDirectory() as directory:
            second_path = Path(directory) / "alternate_valve.txt"
            second_path.write_text("Pressure rating: 120 PSI", encoding="utf-8")
            second = extract_file(second_path)
            second_response = extraction_response(second, pressure_value="120 PSI")
            second_response["attributes"][0]["evidence"]["quote"] = "Pressure rating: 120 PSI"
            client = SequentialGeminiClient(
                [IDENTIFICATION_RESPONSE, extraction_response(txt), second_response]
            )

            result = run_pipeline([SAMPLES / "industrial_valve.txt", second_path], client)

        pressure = result.validation.attributes[0]
        self.assertEqual(pressure.status, "conflict")
        self.assertEqual([value.value for value in pressure.values], ["150 PSI", "120 PSI"])
        self.assertEqual(result.confidence.attributes[0].level, "low")

    def test_unsupported_input_has_clear_pipeline_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unsupported = Path(directory) / "notes.csv"
            unsupported.write_text("not supported", encoding="utf-8")

            with self.assertRaisesRegex(ProductIntelligencePipelineError, "Source extraction failed"):
                run_pipeline([unsupported])


if __name__ == "__main__":
    unittest.main()
