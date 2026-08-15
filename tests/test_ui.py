"""Tests for pure UI formatting helpers (no browser interaction required)."""

import unittest

from src.product_intelligence.attribute_extraction import AttributeEvidence, AttributeExtractionResult
from src.product_intelligence.confidence_scoring import ConfidenceAssessment, ConfidenceScoringResult
from src.product_intelligence.cross_source_validation import (
    CrossSourceValidationResult,
    SourceAttributeValue,
    ValidatedAttribute,
)
from src.product_intelligence.pipeline import ProductIntelligenceResult, SourceSummary
from src.product_intelligence.product_identification import (
    AttributeDefinition,
    ProductIdentificationResult,
)
from src.product_intelligence.ui import build_attribute_rows, build_conflict_rows


def sample_result() -> ProductIntelligenceResult:
    evidence_a = AttributeEvidence(
        source_id="a",
        source_name="datasheet.pdf",
        location="page 1",
        quote="Pressure Rating: 150 PSI",
    )
    evidence_b = AttributeEvidence(
        source_id="b",
        source_name="description.txt",
        location="document",
        quote="Pressure rating: 120 PSI",
    )
    identification = ProductIdentificationResult(
        product_type="Stainless Steel Ball Valve",
        product_category="Industrial valve",
        attributes=[
            AttributeDefinition(name="pressure_rating", label="Pressure Rating"),
            AttributeDefinition(name="temperature_range", label="Temperature Range"),
        ],
    )
    return ProductIntelligenceResult(
        sources=[SourceSummary(source_id="a", source_type="pdf", source_name="datasheet.pdf")],
        product_identification=identification,
        dynamic_attribute_schema=identification.attributes,
        extracted_attributes=[
            AttributeExtractionResult(
                attributes=[
                    {
                        "name": "pressure_rating",
                        "value": "150 PSI",
                        "status": "found",
                        "evidence": evidence_a,
                    },
                    {"name": "temperature_range", "value": None, "status": "not_found"},
                ]
            )
        ],
        validation=CrossSourceValidationResult(
            attributes=[
                ValidatedAttribute(
                    name="pressure_rating",
                    status="conflict",
                    values=[
                        SourceAttributeValue(value="150 PSI", evidence=evidence_a),
                        SourceAttributeValue(value="120 PSI", evidence=evidence_b),
                    ],
                ),
                ValidatedAttribute(name="temperature_range", status="not_found"),
            ]
        ),
        confidence=ConfidenceScoringResult(
            attributes=[
                ConfidenceAssessment(
                    name="pressure_rating",
                    score=0.35,
                    level="low",
                    reasons=["Sources provide different values"],
                ),
                ConfidenceAssessment(
                    name="temperature_range",
                    score=0.0,
                    level="low",
                    reasons=["No source provided a value"],
                ),
            ]
        ),
    )


class UiFormattingTests(unittest.TestCase):
    def test_attribute_rows_make_conflicts_and_missing_values_visible(self) -> None:
        rows = build_attribute_rows(sample_result())

        self.assertEqual(rows[0]["Value"], "150 PSI / 120 PSI")
        self.assertEqual(rows[0]["Status"], "⚠ Conflict")
        self.assertEqual(rows[1]["Value"], "Not found")
        self.assertEqual(rows[1]["Status"], "Not found")

    def test_conflict_rows_preserve_all_values(self) -> None:
        conflicts = build_conflict_rows(sample_result())

        self.assertEqual(conflicts, [{"Attribute": "Pressure Rating", "Values": "150 PSI / 120 PSI"}])


if __name__ == "__main__":
    unittest.main()
