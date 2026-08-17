import json
import unittest
from pathlib import Path

from src.product_intelligence.catalog_input import load_catalog_rows, select_catalog_row
from src.product_intelligence.catalogue_enrichment import (
    AttributeDeliveryMapping,
    AttributeDeliveryMappings,
    EnrichmentSourceDiagnostic,
    enrich_catalogue_row,
)
from src.product_intelligence.delivery_schema import (
    load_delivery_rows,
    load_delivery_schema,
    select_delivery_row,
)
from src.product_intelligence.extraction import NormalizedSource, SourceLocation
from src.product_intelligence.manufacturer_enrichment import (
    ManufacturerSource,
    RetrievalResult,
)
from src.product_intelligence.reference_data import (
    AttributeReference,
    AttributeRule,
    BrandReference,
    ManufacturerReference,
    UOMReference,
)


DATA_DIRECTORY = Path(r"C:\Users\syed7\Downloads")
INPUT_CSV = DATA_DIRECTORY / "Unihack_ Sample Dataset - Input.csv"
DELIVERY_CSV = DATA_DIRECTORY / "Unihack_ Expected Output - Delivery Format.csv"
FIXTURE_PART = "PDSH4816AF"

WEB_URL = "https://www.frigidaire.com/en/p/owner-center/product-support/PDSH4816AF"
PDF_URL = "https://frigidaire.bynder.com/specs/PDSH4816A_EN-pdf.pdf"


class SequentialClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = [json.dumps(response) for response in responses]

    def generate_structured_json(self, prompt: str, response_schema: type) -> str:
        return self.responses.pop(0)


class FixtureProvider:
    def __init__(self, sources: dict[str, NormalizedSource], fail_urls: set[str] | None = None) -> None:
        self.sources = sources
        self.fail_urls = fail_urls or set()
        self.requested_urls: list[str] = []

    def retrieve_source(self, url: str, expected_mpn: str) -> RetrievalResult:
        self.requested_urls.append(url)
        if url in self.fail_urls:
            return RetrievalResult(success=False, error="mock retrieval failure")
        source = self.sources[url]
        return RetrievalResult(
            success=True,
            source=ManufacturerSource(
                url=url,
                source_type=source.source_type,  # type: ignore[arg-type]
                manufacturer_domain="frigidaire.com",
                source_name=source.source_name,
                content=b"verified payload",
                exact_mpn_verified=expected_mpn == FIXTURE_PART,
            ),
        )

    def to_normalized_source(self, source: ManufacturerSource) -> NormalizedSource:
        return next(item for item in self.sources.values() if item.source_name == source.source_name)


def found(name: str, value: str, source: NormalizedSource, quote: str, location: str) -> dict:
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


class CatalogueEnrichmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not INPUT_CSV.exists() or not DELIVERY_CSV.exists():
            raise unittest.SkipTest("The supplied UniHack CSV files are not available.")
        cls.row = select_catalog_row(load_catalog_rows(INPUT_CSV), FIXTURE_PART)
        cls.schema = load_delivery_schema(DELIVERY_CSV)

    def make_sources(self) -> dict[str, NormalizedSource]:
        return {
            WEB_URL: NormalizedSource(
                source_id="web-source",
                source_type="web",
                source_name="frigidaire-product.html",
                extracted_text=(
                    "PDSH4816AF\nMaterial: Stainless Steel\nVoltage Rating: 120 V"
                ),
                locations=(SourceLocation("document"),),
            ),
            PDF_URL: NormalizedSource(
                source_id="pdf-source",
                source_type="pdf",
                source_name="specification.pdf",
                extracted_text=(
                    "Available Products: PDSH4816AF\nMaterial: Stainless Steel\n"
                    "Voltage Rating: 120 V"
                ),
                locations=(SourceLocation("page 1", 1),),
            ),
        }

    def make_client(self, sources: dict[str, NormalizedSource]) -> SequentialClient:
        identification = {
            "product_type": "Dishwasher",
            "product_category": "Dishwasher",
            "attributes": [
                {"name": "material", "label": "Material"},
                {"name": "voltage", "label": "Voltage Rating"},
                {"name": "temperature_range", "label": "Temperature Range"},
            ],
        }
        responses = [identification]
        for source in sources.values():
            location = "page 1" if source.source_type == "pdf" else "document"
            responses.append(
                {
                    "attributes": [
                        found("material", "Stainless Steel", source, "Material: Stainless Steel", location),
                        found("voltage", "120 V", source, "Voltage Rating: 120 V", location),
                        missing("temperature_range"),
                    ]
                }
            )
        return SequentialClient(responses)

    def references(self):
        attributes = AttributeReference(
            {
                "Dishwasher": {
                    "material": AttributeRule(
                        label="Material", allowed_values=("Stainless Steel",)
                    ),
                    "voltage": AttributeRule(
                        label="Voltage Rating", allowed_values=("120",), allowed_uoms=("V",)
                    ),
                }
            }
        )
        return (
            ManufacturerReference(["Approved Manufacturer"]),
            BrandReference(["Approved Brand"]),
            attributes,
            UOMReference({"V": ("v",)}),
        )

    def mappings(self) -> AttributeDeliveryMappings:
        return AttributeDeliveryMappings(
            mappings=[
                AttributeDeliveryMapping(
                    internal_attribute_name="material",
                    delivery_label="Material",
                    slot=1,
                ),
                AttributeDeliveryMapping(
                    internal_attribute_name="voltage",
                    delivery_label="Voltage Rating",
                    slot=2,
                    expected_uom="V",
                ),
                AttributeDeliveryMapping(
                    internal_attribute_name="temperature_range",
                    delivery_label="Temperature Range",
                    slot=3,
                ),
            ]
        )

    def test_complete_fixture_flow_preserves_sources_and_raw_fields(self) -> None:
        sources = self.make_sources()
        provider = FixtureProvider(sources)
        manufacturer, brand, attributes, uoms = self.references()
        expected = select_delivery_row(
            load_delivery_rows(DELIVERY_CSV, self.schema), FIXTURE_PART
        )
        result = enrich_catalogue_row(
            self.row,
            [WEB_URL, PDF_URL],
            self.schema,
            provider=provider,
            client=self.make_client(sources),
            manufacturer_reference=manufacturer,
            brand_reference=brand,
            attribute_reference=attributes,
            uom_reference=uoms,
            attribute_mappings=self.mappings(),
            expected_delivery_row=expected,
        )

        self.assertEqual(provider.requested_urls, [WEB_URL, PDF_URL])
        self.assertIsNotNone(result.pipeline_result)
        self.assertEqual(len(result.delivery_row), 252)
        for field in self.row.raw_fields():
            self.assertEqual(result.delivery_row[field], getattr(self.row, field))
        self.assertEqual(result.delivery_row["ATTRIBUTE_LABEL 1"], "Material")
        self.assertEqual(result.delivery_row["ATTRIBUTE_VALUE 1"], "Stainless Steel")
        self.assertEqual(result.delivery_row["ATTRIBUTE_VALUE 2"], "120")
        self.assertEqual(result.delivery_row["ATTRIBUTE_UOM 2"], "V")
        self.assertEqual(result.delivery_row["MFR URL"], WEB_URL)
        self.assertEqual(result.delivery_row["MANUFACTURER_PART_NUMBER"], FIXTURE_PART)
        self.assertEqual(result.delivery_row["MANUFACTURER_NAME"], "")
        self.assertEqual(result.delivery_row["BRAND_NAME"], "")
        self.assertEqual(result.delivery_row["ATTRIBUTE_VALUE 3"], "")
        self.assertIsNotNone(result.evaluation_comparison)
        self.assertTrue(all(d.review_required for d in result.evaluation_comparison.differences))
        quotes = [
            value.evidence.quote
            for attribute in result.pipeline_result.extracted_attributes
            for value in attribute.attributes
            if value.evidence is not None
        ]
        self.assertTrue(all("Rheem" not in quote for quote in quotes))

    def test_unapproved_attribute_and_uom_do_not_populate_slots(self) -> None:
        sources = self.make_sources()
        provider = FixtureProvider(sources)
        _, _, attributes, _ = self.references()
        mappings = AttributeDeliveryMappings(
            mappings=[
                AttributeDeliveryMapping(
                    internal_attribute_name="material",
                    delivery_label="Material",
                    slot=4,
                ),
                AttributeDeliveryMapping(
                    internal_attribute_name="voltage",
                    delivery_label="Voltage Rating",
                    slot=5,
                    expected_uom="kV",
                ),
            ]
        )
        result = enrich_catalogue_row(
            self.row,
            [WEB_URL, PDF_URL],
            self.schema,
            provider=provider,
            client=self.make_client(sources),
            attribute_reference=attributes,
            attribute_mappings=mappings,
        )

        # Legacy slot metadata is ignored: valid attributes are assigned the
        # next generic slot, while the invalid UOM attribute is skipped.
        self.assertEqual(result.delivery_row["ATTRIBUTE_VALUE 1"], "Stainless Steel")
        self.assertEqual(result.delivery_row["ATTRIBUTE_VALUE 2"], "")
        self.assertTrue(any(item.status == "skipped" for item in result.mapping_diagnostics))

    def test_retrieval_failure_leaves_enrichment_fields_blank(self) -> None:
        sources = self.make_sources()
        provider = FixtureProvider(sources, fail_urls={WEB_URL, PDF_URL})

        result = enrich_catalogue_row(
            self.row,
            [WEB_URL, PDF_URL],
            self.schema,
            provider=provider,
            attribute_mappings=self.mappings(),
        )

        self.assertIsNone(result.pipeline_result)
        self.assertEqual(result.delivery_row["MFR URL"], "")
        self.assertEqual(result.delivery_row["MANUFACTURER_PART_NUMBER"], "")
        self.assertEqual(len(result.source_diagnostics), 2)
        self.assertTrue(all(not diagnostic.success for diagnostic in result.source_diagnostics))
        self.assertEqual(result.delivery_row["Mfg_Part_Num"], FIXTURE_PART)


if __name__ == "__main__":
    unittest.main()
