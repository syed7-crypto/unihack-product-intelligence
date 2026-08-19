import unittest
from types import SimpleNamespace

from src.product_intelligence.attribute_extraction import (
    AttributeEvidence,
    AttributeExtractionResult,
    ExtractedAttribute,
    RejectedAttribute,
)
from src.product_intelligence.catalogue_enrichment import (
    EvaluationComparison,
    EvaluationFieldDifference,
    MappingDiagnostic,
)
from src.product_intelligence.confidence_scoring import (
    ConfidenceAssessment,
    ConfidenceScoringResult,
)
from src.product_intelligence.cross_source_validation import (
    CrossSourceValidationResult,
    SourceAttributeValue,
    ValidatedAttribute,
)
from src.product_intelligence.pipeline import (
    ProductIntelligenceResult,
    SourceSummary,
)
from src.product_intelligence.product_identification import ProductIdentificationResult
from src.product_intelligence.reference_data import (
    CatalogReferenceResolution,
    ReferenceResolutionResult,
)
from src.product_intelligence.review import build_review_report


def source_diagnostic(*, success: bool, error: str | None = None, url: str = "https://source"):
    return SimpleNamespace(
        url=url,
        success=success,
        source_id="source-id" if success else None,
        source_name="source.pdf" if success else None,
        error=error,
    )


def reference_resolution(
    manufacturer_status: str = "resolved",
    brand_status: str = "resolved",
) -> CatalogReferenceResolution:
    def result(reference_type: str, status: str, value: str | None) -> ReferenceResolutionResult:
        return ReferenceResolutionResult(
            input_value=value,
            resolved_value=value if status == "resolved" else None,
            status=status,
            reference_type=reference_type,
            reason="resolved" if status == "resolved" else "No controlled match exists.",
        )

    return CatalogReferenceResolution(
        manufacturer=result("manufacturer", manufacturer_status, "Manufacturer"),
        brands={
            "DIB_Brand": result("brand", brand_status, "Brand"),
        },
    )


def pipeline_result(
    *,
    validation_status: str = "single_source",
    confidence_level: str = "medium",
    value: str = "150 PSI",
) -> ProductIntelligenceResult:
    evidence = AttributeEvidence(
        source_id="source-id",
        source_name="source.pdf",
        location="page 1",
        quote=f"Pressure: {value}",
    )
    definition = {"name": "pressure_rating", "label": "Pressure Rating"}
    identification = ProductIdentificationResult.model_validate(
        {
            "product_type": "Valve",
            "product_category": "Valve",
            "attributes": [definition],
        }
    )
    extracted = AttributeExtractionResult(
        attributes=[
            ExtractedAttribute(
                name="pressure_rating",
                value=value,
                status="found",
                evidence=evidence,
            )
        ]
    )
    values = [SourceAttributeValue(value=value, evidence=evidence)]
    if validation_status == "conflict":
        second_evidence = evidence.model_copy(
            update={"source_id": "source-two", "source_name": "source-two.txt", "quote": "Pressure: 120 PSI"}
        )
        values.append(SourceAttributeValue(value="120 PSI", evidence=second_evidence))
    validation = CrossSourceValidationResult(
        attributes=[ValidatedAttribute(name="pressure_rating", values=values, status=validation_status)]
    )
    confidence = ConfidenceScoringResult(
        attributes=[
            ConfidenceAssessment(
                name="pressure_rating",
                score=0.3 if confidence_level == "low" else 0.6,
                level=confidence_level,
                reasons=["test"],
            )
        ]
    )
    return ProductIntelligenceResult(
        sources=[SourceSummary(source_id="source-id", source_type="pdf", source_name="source.pdf")],
        product_identification=identification,
        dynamic_attribute_schema=identification.attributes,
        extracted_attributes=[extracted],
        validation=validation,
        confidence=confidence,
    )


class ReviewLayerTests(unittest.TestCase):
    def test_exact_mpn_mismatch_blocks_source_and_row(self) -> None:
        report = build_review_report(
            pipeline_result=None,
            source_diagnostics=[
                source_diagnostic(success=False, error="Exact MPN was not found in the source.")
            ],
            reference_resolution=None,
            mapping_diagnostics=[],
            evaluation_comparison=None,
        )

        self.assertEqual(report.status, "blocked")
        self.assertIn("EXACT_MPN_MISMATCH", {issue.code for issue in report.issues})
        self.assertTrue(any(issue.scope == "row" for issue in report.issues))

    def test_all_source_failures_are_blocked(self) -> None:
        report = build_review_report(
            pipeline_result=None,
            source_diagnostics=[
                source_diagnostic(success=False, error="Source returned HTTP status 404."),
                source_diagnostic(success=False, error="Source returned an empty response."),
            ],
            reference_resolution=None,
            mapping_diagnostics=[],
            evaluation_comparison=None,
        )

        self.assertEqual(report.status, "blocked")
        self.assertEqual(sum(issue.scope == "source" for issue in report.issues), 2)

    def test_one_failed_optional_source_with_one_valid_source_remains_ready(self) -> None:
        report = build_review_report(
            pipeline_result=None,
            source_diagnostics=[
                source_diagnostic(success=True),
                source_diagnostic(success=False, error="Exact MPN was not found in the source.", url="https://other"),
            ],
            reference_resolution=None,
            mapping_diagnostics=[],
            evaluation_comparison=None,
        )

        self.assertEqual(report.status, "ready")
        issue = next(issue for issue in report.issues if issue.code == "EXACT_MPN_MISMATCH")
        self.assertEqual(issue.severity, "warning")

    def test_unresolved_manufacturer_and_brand_are_preserved(self) -> None:
        report = build_review_report(
            pipeline_result=None,
            source_diagnostics=[],
            reference_resolution=reference_resolution("unresolved", "unresolved"),
            mapping_diagnostics=[],
            evaluation_comparison=None,
        )

        codes = [issue.code for issue in report.issues]
        self.assertIn("MANUFACTURER_UNRESOLVED", codes)
        self.assertIn("BRAND_UNRESOLVED", codes)
        self.assertEqual(report.status, "needs_review")

    def test_conflict_and_low_confidence_create_attribute_issues(self) -> None:
        result = pipeline_result(validation_status="conflict", confidence_level="low")
        report = build_review_report(
            pipeline_result=result,
            source_diagnostics=[source_diagnostic(success=True)],
            reference_resolution=None,
            mapping_diagnostics=[
                MappingDiagnostic(
                    attribute_name="pressure_rating",
                    slot=1,
                    status="skipped",
                    reason="Conflicting source values require review.",
                )
            ],
            evaluation_comparison=None,
        )

        codes = {issue.code for issue in report.issues}
        self.assertIn("ATTRIBUTE_CONFLICT", codes)
        self.assertIn("LOW_CONFIDENCE", codes)
        self.assertEqual(report.status, "ready")

    def test_unsupported_uom_blocks_attribute_but_missing_fixed_slot_does_not(self) -> None:
        result = pipeline_result()
        report = build_review_report(
            pipeline_result=result,
            source_diagnostics=[source_diagnostic(success=True)],
            reference_resolution=None,
            mapping_diagnostics=[
                MappingDiagnostic(
                    attribute_name="pressure_rating",
                    slot=1,
                    status="skipped",
                    reason="The UOM is not approved for this attribute.",
                )
            ],
            evaluation_comparison=None,
        )

        codes = {issue.code for issue in report.issues}
        self.assertIn("UOM_NOT_APPROVED", codes)
        self.assertEqual(report.status, "ready")

        missing_mapping = build_review_report(
            pipeline_result=result,
            source_diagnostics=[source_diagnostic(success=True)],
            reference_resolution=None,
            mapping_diagnostics=[],
            evaluation_comparison=None,
        )
        self.assertNotIn("ATTRIBUTE_MAPPING_MISSING", {issue.code for issue in missing_mapping.issues})

    def test_evidence_failure_is_blocked_and_invalid_evidence_is_not_approved(self) -> None:
        report = build_review_report(
            pipeline_result=None,
            source_diagnostics=[
                source_diagnostic(
                    success=False,
                    error="Pipeline failed after source verification: Evidence quote is not in the source.",
                )
            ],
            reference_resolution=None,
            mapping_diagnostics=[],
            evaluation_comparison=None,
        )

        self.assertEqual(report.status, "blocked")
        self.assertIn("EVIDENCE_QUOTE_NOT_FOUND", {issue.code for issue in report.issues})

    def test_fully_validated_attribute_has_no_review_issue(self) -> None:
        result = pipeline_result()
        report = build_review_report(
            pipeline_result=result,
            source_diagnostics=[source_diagnostic(success=True)],
            reference_resolution=None,
            mapping_diagnostics=[
                MappingDiagnostic(
                    attribute_name="pressure_rating",
                    slot=1,
                    status="mapped",
                    reason="Mapped using official controlled mapping and validated evidence.",
                )
            ],
            evaluation_comparison=None,
        )

        self.assertEqual(report.status, "ready")

    def test_rejected_attribute_is_reviewed_without_becoming_a_delivery_value(self) -> None:
        result = pipeline_result()
        result.extracted_attributes[0].rejected_attributes = [
            RejectedAttribute(
                name="arbor_type",
                code="EVIDENCE_VALUE_NOT_IN_QUOTE",
                message="The value is not supported by its evidence quote.",
                proposed_value="Arbor type guessed by Gemini",
            )
        ]
        report = build_review_report(
            pipeline_result=result,
            source_diagnostics=[source_diagnostic(success=True)],
            reference_resolution=None,
            mapping_diagnostics=[],
            evaluation_comparison=None,
        )

        issue = next(issue for issue in report.issues if issue.code == "EVIDENCE_VALUE_NOT_IN_QUOTE")
        self.assertEqual(issue.scope, "attribute")
        self.assertEqual(report.status, "ready")
        self.assertEqual(result.validation.attributes[0].name, "pressure_rating")

    def test_evaluation_differences_are_non_delivery_issues(self) -> None:
        comparison = EvaluationComparison(
            mfg_part_num="PDSH4816AF",
            matches=False,
            differences=[
                EvaluationFieldDifference(
                    column="MANUFACTURER_NAME",
                    generated_value="",
                    expected_value="Rheem Manufacturing",
                )
            ],
        )
        report = build_review_report(
            pipeline_result=None,
            source_diagnostics=[],
            reference_resolution=None,
            mapping_diagnostics=[],
            evaluation_comparison=comparison,
        )

        self.assertEqual(report.status, "ready")
        self.assertEqual(report.issues[0].scope, "evaluation")
        self.assertFalse(report.issues[0].affects_delivery)

    def test_multiple_independent_issues_are_preserved(self) -> None:
        report = build_review_report(
            pipeline_result=None,
            source_diagnostics=[source_diagnostic(success=False, error="Source returned HTTP status 500.")],
            reference_resolution=reference_resolution("unresolved", "unresolved"),
            mapping_diagnostics=[],
            evaluation_comparison=None,
        )

        codes = {issue.code for issue in report.issues}
        self.assertTrue({"SOURCE_HTTP_ERROR", "MANUFACTURER_UNRESOLVED", "BRAND_UNRESOLVED"}.issubset(codes))


if __name__ == "__main__":
    unittest.main()
