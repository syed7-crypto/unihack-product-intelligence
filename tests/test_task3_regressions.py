"""Generic regression fixtures for TASK 3 failure categories.

These tests intentionally use synthetic identifiers rather than catalogue MPNs.
"""

import json
import unittest

from src.product_intelligence.attribute_extraction import extract_attribute_values
from src.product_intelligence.catalog_input import CatalogInputRow
from src.product_intelligence.extraction import NormalizedSource, SourceLocation
from src.product_intelligence.manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    RetrievedPayload,
)
from src.product_intelligence.product_identification import ProductIdentificationResult
from src.product_intelligence.source_discovery import (
    InMemorySourceSearchProvider,
    ManufacturerSourcePolicy,
    SearchResult,
    discover_and_verify_sources,
)


class FakeClient:
    def __init__(self, response: dict) -> None:
        self.response = json.dumps(response)

    def generate_structured_json(self, prompt: str, response_schema: type) -> str:
        return self.response


def schema() -> ProductIdentificationResult:
    return ProductIdentificationResult.model_validate(
        {
            "product_type": "Test Product",
            "product_category": "Test Category",
            "attributes": [
                {"name": "material", "label": "Material"},
                {"name": "voltage", "label": "Voltage"},
            ],
        }
    )


def source() -> NormalizedSource:
    return NormalizedSource(
        source_id="task3-source",
        source_type="txt",
        source_name="task3.txt",
        extracted_text="Material: Stainless   Steel\nVoltage: 120 V",
        locations=(SourceLocation("document"),),
    )


def found(name: str, value: str, quote: str) -> dict:
    return {
        "name": name,
        "value": value,
        "status": "found",
        "evidence": {
            "source_id": "task3-source",
            "source_name": "task3.txt",
            "location": "document",
            "quote": quote,
        },
    }


class Task3RegressionTests(unittest.TestCase):
    def test_one_bad_attribute_does_not_discard_other_verified_attribute(self) -> None:
        result = extract_attribute_values(
            source(),
            schema(),
            FakeClient(
                {
                    "attributes": [
                        found("material", "Carbon Steel", "Material: Stainless Steel"),
                        found("voltage", "120 V", "Voltage: 120 V"),
                    ]
                }
            ),
        )

        self.assertEqual(result.attributes[0].status, "not_found")
        self.assertEqual(result.attributes[1].status, "found")
        self.assertEqual(result.rejected_attributes[0].code, "EVIDENCE_VALUE_NOT_IN_QUOTE")

    def test_equivalent_case_and_whitespace_evidence_is_accepted(self) -> None:
        result = extract_attribute_values(
            source(),
            schema(),
            FakeClient(
                {
                    "attributes": [
                        found("material", " stainless steel ", "Material: Stainless Steel"),
                        {"name": "voltage", "status": "not_found", "value": None, "evidence": None},
                    ]
                }
            ),
        )

        self.assertEqual(result.attributes[0].status, "found")

    def test_inaccessible_source_is_not_treated_as_mpn_mismatch(self) -> None:
        url = "https://official.example/product/GENERIC-1"
        policy = ManufacturerSourcePolicy(
            manufacturer_name="Example Manufacturer",
            approved_domains=("official.example",),
            query_templates=("{part_number} {manufacturer}",),
        )
        search = InMemorySourceSearchProvider(
            {"GENERIC-1 Example Manufacturer": [SearchResult(url=url)]}
        )
        provider = ManufacturerEnrichmentProvider(
            fetcher=lambda _url, _timeout: (_ for _ in ()).throw(TimeoutError("network timeout"))
        )
        row = CatalogInputRow(
            Mfg_Part_Num="GENERIC-1",
            Part_Desc="test",
            E1_Brand="-- Unbranded --",
            Unilog_Brand="-- No Unilog Brand --",
            DIB_Brand="-- No DIB Brand --",
            Part_Manuf="Example Manufacturer",
        )

        result = discover_and_verify_sources(row, policy, search, provider)

        self.assertEqual(result.failure_code, "SOURCE_RETRIEVAL_FAILED")
        self.assertEqual(result.diagnostics[0].code, "SOURCE_RETRIEVAL_FAILED")

    def test_completed_search_without_trustworthy_source_remains_unresolved(self) -> None:
        row = CatalogInputRow(
            Mfg_Part_Num="GENERIC-2",
            Part_Desc="test",
            E1_Brand="-- Unbranded --",
            Unilog_Brand="-- No Unilog Brand --",
            DIB_Brand="-- No DIB Brand --",
            Part_Manuf="Unknown Manufacturer",
        )
        policy = ManufacturerSourcePolicy(
            manufacturer_name="Example Manufacturer",
            approved_domains=("official.example",),
            query_templates=("{part_number} {manufacturer}",),
        )

        result = discover_and_verify_sources(
            row, policy, InMemorySourceSearchProvider({}), ManufacturerEnrichmentProvider()
        )

        self.assertEqual(result.failure_code, "NO_TRUSTWORTHY_SOURCE")
        self.assertEqual(result.verified_sources, [])


if __name__ == "__main__":
    unittest.main()
