"""Tests for deterministic cross-source validation."""

import unittest

from src.product_intelligence.attribute_extraction import (
    AttributeEvidence,
    AttributeExtractionResult,
    ExtractedAttribute,
)
from src.product_intelligence.cross_source_validation import validate_cross_source
from src.product_intelligence.product_identification import ProductIdentificationResult


def product_schema() -> ProductIdentificationResult:
    return ProductIdentificationResult.model_validate(
        {
            "product_type": "Industrial Valve",
            "product_category": "Industrial valve",
            "attributes": [
                {"name": "pressure_rating", "label": "Pressure Rating"},
                {"name": "material", "label": "Material"},
                {"name": "connection_type", "label": "Connection Type"},
            ],
        }
    )


def source_value(
    source_id: str,
    source_name: str,
    value: str,
    location: str,
) -> ExtractedAttribute:
    return ExtractedAttribute(
        name="pressure_rating",
        value=value,
        status="found",
        evidence=AttributeEvidence(
            source_id=source_id,
            source_name=source_name,
            location=location,
            quote=f"Maximum working pressure: {value}",
        ),
    )


def not_found(name: str) -> ExtractedAttribute:
    return ExtractedAttribute(name=name, value=None, status="not_found", evidence=None)


def extraction(*attributes: ExtractedAttribute) -> AttributeExtractionResult:
    return AttributeExtractionResult(attributes=list(attributes))


class CrossSourceValidationTests(unittest.TestCase):
    def test_equivalent_values_in_different_units_are_consistent(self) -> None:
        result = validate_cross_source(
            [
                extraction(source_value("a", "metric.txt", "2 m", "document"), not_found("material"), not_found("connection_type")),
                extraction(source_value("b", "centimeters.txt", "200 cm", "document"), not_found("material"), not_found("connection_type")),
            ],
            product_schema(),
        )

        pressure = result.attributes[0]
        self.assertEqual(pressure.status, "consistent")
        self.assertEqual([item.value for item in pressure.values], ["2 m", "200 cm"])
        self.assertEqual([item.evidence.source_name for item in pressure.values], ["metric.txt", "centimeters.txt"])

    def test_different_dimensions_remain_a_conflict(self) -> None:
        result = validate_cross_source(
            [
                extraction(source_value("a", "length.txt", "2 m", "document"), not_found("material"), not_found("connection_type")),
                extraction(source_value("b", "mass.txt", "2 kg", "document"), not_found("material"), not_found("connection_type")),
            ],
            product_schema(),
        )

        self.assertEqual(result.attributes[0].status, "conflict")

    def test_non_measurement_values_keep_existing_comparison_behavior(self) -> None:
        result = validate_cross_source(
            [
                extraction(source_value("a", "a.txt", "IP67", "document"), not_found("material"), not_found("connection_type")),
                extraction(source_value("b", "b.txt", "ip67", "document"), not_found("material"), not_found("connection_type")),
            ],
            product_schema(),
        )

        self.assertEqual(result.attributes[0].status, "consistent")

    def test_identical_values_are_consistent(self) -> None:
        result = validate_cross_source(
            [
                extraction(source_value("a", "datasheet.pdf", "150 PSI", "page 3"), not_found("material"), not_found("connection_type")),
                extraction(source_value("b", "product.txt", "150 PSI", "document"), not_found("material"), not_found("connection_type")),
            ],
            product_schema(),
        )

        pressure = result.attributes[0]
        self.assertEqual(pressure.status, "consistent")
        self.assertEqual([item.value for item in pressure.values], ["150 PSI", "150 PSI"])
        self.assertIsNone(pressure.conflict)

    def test_different_values_are_conflict(self) -> None:
        result = validate_cross_source(
            [
                extraction(source_value("a", "datasheet.pdf", "150 PSI", "page 3"), not_found("material"), not_found("connection_type")),
                extraction(source_value("b", "product.txt", "120 PSI", "document"), not_found("material"), not_found("connection_type")),
            ],
            product_schema(),
        )

        pressure = result.attributes[0]
        self.assertEqual(pressure.status, "conflict")
        self.assertIsNotNone(pressure.conflict)
        assert pressure.conflict is not None
        self.assertEqual(set(pressure.conflict.distinct_values), {"150 PSI", "120 PSI"})

    def test_case_differences_are_consistent(self) -> None:
        result = validate_cross_source(
            [
                extraction(source_value("a", "a.txt", "Stainless Steel", "document"), not_found("material"), not_found("connection_type")),
                extraction(source_value("b", "b.txt", "stainless steel", "document"), not_found("material"), not_found("connection_type")),
            ],
            product_schema(),
        )

        self.assertEqual(result.attributes[0].status, "consistent")

    def test_one_source_is_single_source(self) -> None:
        result = validate_cross_source(
            [extraction(source_value("a", "datasheet.pdf", "150 PSI", "page 3"), not_found("material"), not_found("connection_type"))],
            product_schema(),
        )

        self.assertEqual(result.attributes[0].status, "single_source")
        self.assertEqual(result.attributes[0].values[0].evidence.source_name, "datasheet.pdf")

    def test_missing_from_all_sources_is_not_found(self) -> None:
        result = validate_cross_source(
            [
                extraction(not_found("pressure_rating"), not_found("material"), not_found("connection_type")),
                extraction(not_found("pressure_rating"), not_found("material"), not_found("connection_type")),
            ],
            product_schema(),
        )

        self.assertEqual([attribute.status for attribute in result.attributes], ["not_found"] * 3)
        self.assertEqual(result.attributes[0].values, [])

    def test_three_sources_preserve_all_conflicting_values(self) -> None:
        result = validate_cross_source(
            [
                extraction(source_value("a", "a.pdf", "150 PSI", "page 1"), not_found("material"), not_found("connection_type")),
                extraction(source_value("b", "b.txt", "120 PSI", "document"), not_found("material"), not_found("connection_type")),
                extraction(source_value("c", "c.json", "175 PSI", "document"), not_found("material"), not_found("connection_type")),
            ],
            product_schema(),
        )

        pressure = result.attributes[0]
        self.assertEqual(pressure.status, "conflict")
        self.assertEqual([item.evidence.source_name for item in pressure.values], ["a.pdf", "b.txt", "c.json"])
        self.assertEqual(len(pressure.conflict.distinct_values), 3)  # type: ignore[union-attr]

    def test_evidence_from_every_source_is_preserved(self) -> None:
        result = validate_cross_source(
            [
                extraction(source_value("a", "datasheet.pdf", "150 PSI", "page 3"), not_found("material"), not_found("connection_type")),
                extraction(source_value("b", "product.txt", "120 PSI", "document"), not_found("material"), not_found("connection_type")),
            ],
            product_schema(),
        )

        evidence = result.attributes[0].values
        self.assertEqual([(item.evidence.source_id, item.evidence.location) for item in evidence], [("a", "page 3"), ("b", "document")])


if __name__ == "__main__":
    unittest.main()
