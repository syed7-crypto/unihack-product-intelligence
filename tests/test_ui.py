"""Tests for pure UI formatting helpers (no browser interaction required)."""

import unittest
from unittest.mock import patch

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
from src.product_intelligence.delivery_schema import load_delivery_schema
from src.product_intelligence.ui import (
    CANONICAL_DELIVERY_SCHEMA_PATH,
    DEMO_CATALOGUE_PATH,
    MAX_PUBLIC_DEMO_ROWS,
    PUBLIC_DEMO_LIMIT_MESSAGE,
    PUBLIC_DEMO_NOTICE,
    _load_demo_catalogue,
    _public_demo_limit_error,
    _render_catalogue_preview,
    build_attribute_rows,
    build_conflict_rows,
)


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
    def test_demo_catalogue_loads_exactly_ten_rows(self) -> None:
        first = _load_demo_catalogue()
        second = _load_demo_catalogue()

        self.assertEqual(DEMO_CATALOGUE_PATH.name, "input.csv")
        self.assertEqual(len(first), MAX_PUBLIC_DEMO_ROWS)
        self.assertEqual(
            [row.Mfg_Part_Num for row in first],
            [row.Mfg_Part_Num for row in second],
        )

    def test_demo_rows_use_the_shared_catalogue_preview_renderer(self) -> None:
        demo_rows = _load_demo_catalogue()

        with patch("src.product_intelligence.ui.st.dataframe") as dataframe:
            rendered_rows = _render_catalogue_preview(demo_rows)

        self.assertIs(rendered_rows, demo_rows)
        dataframe.assert_called_once()
        preview_payload = dataframe.call_args.args[0]
        self.assertEqual(
            [row["Mfg_Part_Num"] for row in preview_payload],
            [row.Mfg_Part_Num for row in demo_rows[:8]],
        )

    def test_public_demo_allows_ten_rows(self) -> None:
        self.assertIsNone(_public_demo_limit_error(10))

    def test_public_demo_rejects_rows_before_batch_execution(self) -> None:
        message = _public_demo_limit_error(11)
        self.assertEqual(message, PUBLIC_DEMO_LIMIT_MESSAGE)
        self.assertEqual(_public_demo_limit_error(1000), message)

    def test_public_demo_messaging_explains_deployment_limit(self) -> None:
        self.assertIn("🚀 **Demo Mode**", PUBLIC_DEMO_NOTICE)
        self.assertIn("public prototype", PUBLIC_DEMO_NOTICE)
        self.assertIn("external search and AI APIs", PUBLIC_DEMO_NOTICE)
        self.assertIn("run the project locally", PUBLIC_DEMO_NOTICE)
        self.assertIn("**Demo limit reached**", PUBLIC_DEMO_LIMIT_MESSAGE)
        self.assertIn("supports up to 10 products", PUBLIC_DEMO_LIMIT_MESSAGE)
        self.assertNotIn("can only handle 10", PUBLIC_DEMO_NOTICE.lower())
        self.assertNotIn("can only handle 10", PUBLIC_DEMO_LIMIT_MESSAGE.lower())

    def test_non_public_mode_remains_unrestricted(self) -> None:
        self.assertIsNone(_public_demo_limit_error(1000, enabled=False))

    def test_ui_uses_locked_canonical_252_column_schema(self) -> None:
        schema = load_delivery_schema(CANONICAL_DELIVERY_SCHEMA_PATH)

        self.assertEqual(len(schema.columns), 252)
        self.assertEqual(schema.columns[0], "MFR URL")
        self.assertEqual(schema.columns[-1], "Actual Image (Yes/No)")

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
