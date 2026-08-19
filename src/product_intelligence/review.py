"""Small, deterministic review/exception model for catalogue enrichment."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from .catalog_input import is_placeholder_brand
from .pipeline import ProductIntelligenceResult
from .reference_data import CatalogReferenceResolution, ReferenceResolutionResult


ReviewScope = Literal["row", "attribute", "source", "evaluation"]
ReviewStatusValue = Literal["ready", "needs_review", "blocked", "failed"]
ReviewSeverity = Literal["info", "warning", "blocking", "error"]


class ReviewIssue(BaseModel):
    """One actionable explanation for review or blocked delivery."""

    code: str = Field(min_length=1)
    severity: ReviewSeverity
    scope: ReviewScope
    message: str = Field(min_length=1)
    attribute_name: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    current_value: str | None = None
    affects_delivery: bool = True


class ReviewReport(BaseModel):
    """Aggregate review state; it never changes the underlying values."""

    status: ReviewStatusValue = "ready"
    issues: list[ReviewIssue] = Field(default_factory=list)


def build_review_report(
    *,
    pipeline_result: ProductIntelligenceResult | None,
    source_diagnostics: Sequence[object],
    reference_resolution: CatalogReferenceResolution | None,
    mapping_diagnostics: Sequence[object],
    evaluation_comparison: object | None,
    taxonomy_resolution: ReferenceResolutionResult | None = None,
) -> ReviewReport:
    """Build review issues from existing pipeline and enrichment results."""
    issues: list[ReviewIssue] = []

    def add(issue: ReviewIssue) -> None:
        key = (
            issue.code,
            issue.scope,
            issue.attribute_name,
            issue.source_id,
            issue.source_name,
            issue.current_value,
        )
        if not any(
            (
                existing.code,
                existing.scope,
                existing.attribute_name,
                existing.source_id,
                existing.source_name,
                existing.current_value,
            )
            == key
            for existing in issues
        ):
            issues.append(issue)

    successful_sources = [
        item for item in source_diagnostics if bool(getattr(item, "success", False))
    ]
    failed_sources = [
        item for item in source_diagnostics if not bool(getattr(item, "success", False))
    ]
    for diagnostic in failed_sources:
        error = str(getattr(diagnostic, "error", None) or "Source retrieval failed.")
        explicit_code = getattr(diagnostic, "code", None)
        if explicit_code:
            code = explicit_code
            severity = (
                "warning"
                if explicit_code in {"NO_TRUSTWORTHY_SOURCE", "IDENTITY_UNRESOLVED"}
                or successful_sources
                else "blocking"
            )
            scope = "source"
        else:
            code, severity, scope = _source_error_details(error, bool(successful_sources))
        add(
            ReviewIssue(
                code=code,
                severity=severity,
                scope=scope,
                message=error,
                source_id=getattr(diagnostic, "source_id", None),
                source_name=getattr(diagnostic, "source_name", None),
                current_value=error,
                affects_delivery=not bool(successful_sources),
            )
        )

    if reference_resolution is not None:
        manufacturer = reference_resolution.manufacturer
        if manufacturer.status != "resolved":
            add(
                ReviewIssue(
                    code="MANUFACTURER_UNRESOLVED",
                    severity="warning",
                    scope="row",
                    message=manufacturer.reason,
                    current_value=manufacturer.input_value,
                    affects_delivery=True,
                )
            )
        for field, brand in reference_resolution.brands.items():
            if brand.status != "resolved" and _brand_requires_review(brand):
                add(
                    ReviewIssue(
                        code="BRAND_UNRESOLVED",
                        severity="warning",
                        scope="row",
                        message=f"{field}: {brand.reason}",
                        current_value=brand.input_value,
                        affects_delivery=True,
                    )
                )

    if taxonomy_resolution is not None and taxonomy_resolution.status != "resolved":
        add(
            ReviewIssue(
                code="TAXONOMY_UNRESOLVED",
                severity="warning",
                scope="row",
                message=taxonomy_resolution.reason,
                current_value=taxonomy_resolution.input_value,
                affects_delivery=True,
            )
        )

    if pipeline_result is not None:
        for diagnostic in pipeline_result.diagnostics:
            add(
                ReviewIssue(
                    code=diagnostic.code,
                    severity="warning" if pipeline_result.extracted_attributes else "blocking",
                    scope="source",
                    message=diagnostic.message,
                    source_id=diagnostic.source_id,
                    source_name=diagnostic.source_name,
                    affects_delivery=not bool(pipeline_result.extracted_attributes),
                )
            )
        for extraction in pipeline_result.extracted_attributes:
            for rejected in extraction.rejected_attributes:
                add(
                    ReviewIssue(
                        code=rejected.code,
                        severity="blocking",
                        scope="attribute",
                        message=rejected.message,
                        attribute_name=rejected.name,
                        current_value=rejected.proposed_value,
                        affects_delivery=True,
                    )
                )
        mapped_names = {
            str(getattr(item, "attribute_name"))
            for item in mapping_diagnostics
            if getattr(item, "status", None) == "mapped"
        }
        for attribute in pipeline_result.validation.attributes:
            if attribute.status == "conflict":
                add(
                    ReviewIssue(
                        code="ATTRIBUTE_CONFLICT",
                        severity="blocking",
                        scope="attribute",
                        message="Sources provide different values; no value was selected.",
                        attribute_name=attribute.name,
                        current_value=", ".join(item.value for item in attribute.values),
                        affects_delivery=True,
                    )
                )

            confidence = next(
                (
                    item
                    for item in pipeline_result.confidence.attributes
                    if item.name == attribute.name
                ),
                None,
            )
            if (
                confidence is not None
                and confidence.level == "low"
                and attribute.status != "not_found"
            ):
                add(
                    ReviewIssue(
                        code="LOW_CONFIDENCE",
                        severity="warning",
                        scope="attribute",
                        message="Confidence is low; the attribute requires review.",
                        attribute_name=attribute.name,
                        current_value=(attribute.values[0].value if attribute.values else None),
                        affects_delivery=True,
                    )
                )

        for diagnostic in mapping_diagnostics:
            if getattr(diagnostic, "status", None) != "skipped":
                continue
            attribute_name = getattr(diagnostic, "attribute_name", None)
            reason = str(getattr(diagnostic, "reason", "Mapping was skipped."))
            explicit_code = getattr(diagnostic, "code", None)
            code, severity = (
                (explicit_code, "blocking")
                if explicit_code and explicit_code not in {"ATTRIBUTE_NOT_FOUND"}
                else _mapping_issue_details(reason)
            )
            if code is not None:
                current_value = _validated_value(pipeline_result, attribute_name)
                add(
                    ReviewIssue(
                        code=code,
                        severity=severity,
                        scope="attribute",
                        message=reason,
                        attribute_name=attribute_name,
                        current_value=current_value,
                        affects_delivery=True,
                    )
                )

    if (
        pipeline_result is None
        and source_diagnostics
        and not successful_sources
        and not all(
            "identity/source policy resolution unknown" in str(getattr(item, "error", "")).casefold()
            for item in source_diagnostics
        )
    ):
        add(
            ReviewIssue(
                code="SOURCE_RETRIEVAL_FAILED",
                severity="blocking",
                scope="row",
                message="No verified manufacturer source was available for the row.",
                affects_delivery=True,
            )
        )

    if evaluation_comparison is not None:
        for difference in getattr(evaluation_comparison, "differences", []):
            add(
                ReviewIssue(
                    code="EXPECTED_OUTPUT_DISCREPANCY",
                    severity="info",
                    scope="evaluation",
                    message="Generated delivery value differs from the evaluation row.",
                    current_value=getattr(difference, "generated_value", None),
                    affects_delivery=False,
                )
            )

    return ReviewReport(status=_report_status(issues), issues=issues)


def _source_error_details(error: str, has_successful_source: bool) -> tuple[str, ReviewSeverity, ReviewScope]:
    lowered = error.casefold()
    if "identity/source policy resolution unknown" in lowered:
        return "IDENTITY_UNRESOLVED", "warning", "row"
    if "pipeline failed" in lowered:
        code = _pipeline_error_code(error)
        if code == "PIPELINE_FAILED":
            return code, "error", "row"
        return code, "blocking", "row"
    if "authoritative identifier mapping" in lowered or "identifier mapping" in lowered:
        code = "AUTHORITATIVE_IDENTIFIER_MAPPING_MISSING"
    elif "exact mpn" in lowered or "exact part" in lowered:
        code = "EXACT_MPN_MISMATCH"
    elif "http status" in lowered:
        code = "SOURCE_HTTP_ERROR"
    elif "empty" in lowered:
        code = "SOURCE_EMPTY"
    elif "unsupported" in lowered:
        code = "SOURCE_UNSUPPORTED_TYPE"
    elif "normalization" in lowered:
        code = "SOURCE_NORMALIZATION_FAILED"
    else:
        code = "SOURCE_RETRIEVAL_FAILED"
    severity: ReviewSeverity = "warning" if has_successful_source else "blocking"
    return code, severity, "source"


def _brand_requires_review(brand: ReferenceResolutionResult) -> bool:
    """Only review a brand candidate when the catalogue supplied one."""
    value = brand.input_value
    return bool(value and not is_placeholder_brand(value))


def _pipeline_error_code(error: str) -> str:
    lowered = error.casefold()
    if "quote" in lowered and "not" in lowered:
        return "EVIDENCE_QUOTE_NOT_FOUND"
    if "value" in lowered and "quote" in lowered:
        return "EVIDENCE_VALUE_NOT_IN_QUOTE"
    if "location" in lowered:
        return "EVIDENCE_LOCATION_INVALID"
    if "evidence" in lowered and "source" in lowered:
        return "EVIDENCE_SOURCE_MISMATCH"
    if "found attributes require" in lowered or "evidence" in lowered:
        return "EVIDENCE_MISSING"
    return "PIPELINE_FAILED"


def _mapping_issue_details(reason: str) -> tuple[str | None, ReviewSeverity]:
    lowered = reason.casefold()
    if "attribute_slot_limit_exceeded" in lowered:
        return "ATTRIBUTE_SLOT_LIMIT_EXCEEDED", "warning"
    if "conflicting" in lowered:
        return "ATTRIBUTE_CONFLICT", "blocking"
    if "uom" in lowered:
        return "UOM_NOT_APPROVED", "blocking"
    if "attribute reference" in lowered:
        return "ATTRIBUTE_REFERENCE_MISSING", "blocking"
    if "approved" in lowered or "allowed-value" in lowered:
        return "ATTRIBUTE_VALUE_NOT_APPROVED", "blocking"
    if "mapping" in lowered:
        return "ATTRIBUTE_MAPPING_MISSING", "blocking"
    return None, "warning"


def _validated_value(pipeline_result: ProductIntelligenceResult, name: str | None) -> str | None:
    if not name:
        return None
    for attribute in pipeline_result.validation.attributes:
        if attribute.name == name and attribute.values:
            return attribute.values[0].value
    return None


def _report_status(issues: list[ReviewIssue]) -> ReviewStatusValue:
    # Attribute-level failures describe fields omitted from delivery; they do
    # not invalidate independently verified product/source fields.  Row/source
    # issues remain the status gate, and exact-MPN/no-source failures therefore
    # continue to block fail-closed.
    row_source_issues = [
        issue for issue in issues
        if issue.affects_delivery and issue.scope in {"row", "source"}
    ]
    if any(issue.severity == "error" for issue in row_source_issues):
        return "failed"
    if any(
        issue.severity == "blocking" for issue in row_source_issues
    ):
        return "blocked"
    if row_source_issues:
        return "needs_review"
    return "ready"
