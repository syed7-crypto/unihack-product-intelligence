"""Tests for deterministic confidence scoring."""

import unittest

from src.product_intelligence.attribute_extraction import AttributeEvidence
from src.product_intelligence.confidence_scoring import calculate_confidence
from src.product_intelligence.cross_source_validation import (
    ConflictInfo,
    CrossSourceValidationResult,
    SourceAttributeValue,
    ValidatedAttribute,
)


def evidence(source_id: str, location: str | None = "page 3") -> AttributeEvidence:
    return AttributeEvidence(
        source_id=source_id,
        source_name=f"{source_id}.txt",
        location=location,
        quote="Maximum working pressure: 150 PSI",
    )


def validated(
    status: str,
    source_ids: list[str],
    locations: list[str | None] | None = None,
) -> ValidatedAttribute:
    locations = locations or ["page 3"] * len(source_ids)
    values = [
        SourceAttributeValue(value="150 PSI", evidence=evidence(source_id, location))
        for source_id, location in zip(source_ids, locations)
    ]
    conflict = None
    if status == "conflict":
        conflict = ConflictInfo(distinct_values=["150 PSI", "120 PSI"])
    return ValidatedAttribute(
        name="pressure_rating",
        values=values,
        status=status,  # type: ignore[arg-type]
        conflict=conflict,
    )


class ConfidenceScoringTests(unittest.TestCase):
    def test_strong_evidence_and_agreement_is_high(self) -> None:
        result = calculate_confidence(
            CrossSourceValidationResult(
                attributes=[validated("consistent", ["a", "b"])]
            )
        )

        assessment = result.attributes[0]
        self.assertEqual(assessment.score, 0.9)
        self.assertEqual(assessment.level, "high")
        self.assertIn("Multiple sources agree", assessment.reasons)

    def test_one_direct_source_is_medium(self) -> None:
        result = calculate_confidence(
            CrossSourceValidationResult(
                attributes=[validated("single_source", ["a"])]
            )
        )

        assessment = result.attributes[0]
        self.assertEqual(assessment.score, 0.7)
        self.assertEqual(assessment.level, "medium")
        self.assertIn("Only one source provides the attribute", assessment.reasons)

    def test_conflicting_sources_are_low(self) -> None:
        result = calculate_confidence(
            CrossSourceValidationResult(
                attributes=[validated("conflict", ["a", "b"])]
            )
        )

        assessment = result.attributes[0]
        self.assertEqual(assessment.score, 0.35)
        self.assertEqual(assessment.level, "low")
        self.assertIn("Conflict requires review", assessment.reasons)

    def test_not_found_has_zero_confidence(self) -> None:
        result = calculate_confidence(
            CrossSourceValidationResult(
                attributes=[ValidatedAttribute(name="pressure_rating", status="not_found")]
            )
        )

        assessment = result.attributes[0]
        self.assertEqual(assessment.score, 0.0)
        self.assertEqual(assessment.level, "low")
        self.assertIn("No source provided a value", assessment.reasons)

    def test_location_is_an_additional_evidence_signal(self) -> None:
        with_location = calculate_confidence(
            CrossSourceValidationResult(
                attributes=[validated("single_source", ["a"], ["page 3"])]
            )
        ).attributes[0]
        without_location = calculate_confidence(
            CrossSourceValidationResult(
                attributes=[validated("single_source", ["a"], [None])]
            )
        ).attributes[0]

        self.assertEqual(with_location.score, 0.7)
        self.assertEqual(without_location.score, 0.65)
        self.assertIn("Source location metadata was available", with_location.reasons)

    def test_all_scores_are_bounded(self) -> None:
        result = calculate_confidence(
            CrossSourceValidationResult(
                attributes=[
                    validated("consistent", ["a", "b", "c"]),
                    validated("single_source", ["a"]),
                    validated("conflict", ["a", "b", "c"]),
                    ValidatedAttribute(name="missing", status="not_found"),
                ]
            )
        )

        for assessment in result.attributes:
            self.assertGreaterEqual(assessment.score, 0.0)
            self.assertLessEqual(assessment.score, 1.0)


if __name__ == "__main__":
    unittest.main()
