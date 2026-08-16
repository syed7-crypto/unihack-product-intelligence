"""Deterministic, row-isolated orchestration for catalogue enrichment."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .catalog_input import CatalogInputRow, load_catalog_rows
from .catalogue_enrichment import (
    AttributeDeliveryMappings,
    CatalogueEnrichmentError,
    CatalogueEnrichmentResult,
    EnrichmentSourceDiagnostic,
    enrich_catalogue_row,
)
from .delivery_output import map_raw_fields_to_delivery
from .delivery_schema import DeliverySchema
from .manufacturer_enrichment import ManufacturerEnrichmentProvider
from .reference_data import AttributeReference, BrandReference, ManufacturerReference, UOMReference
from .review import ReviewIssue, ReviewReport


SourceURLResolver = Callable[[CatalogInputRow], Sequence[str]]
ExpectedRowResolver = Callable[[CatalogInputRow], Mapping[str, str] | None]
RowEnricher = Callable[..., CatalogueEnrichmentResult]


class BatchReviewIssue(BaseModel):
    """A review issue retaining the row that produced it."""

    row_index: int = Field(ge=0)
    mfg_part_num: str
    issue: ReviewIssue


class BatchEvaluationDiagnostic(BaseModel):
    """An evaluation comparison retaining its input-row identity."""

    row_index: int = Field(ge=0)
    mfg_part_num: str
    comparison: Any


class BatchResult(BaseModel):
    """Ordered outcomes and deterministic summary for one catalogue batch."""

    total_rows: int = Field(ge=0)
    processed_rows: int = Field(ge=0)
    ready_rows: int = Field(ge=0)
    needs_review_rows: int = Field(ge=0)
    blocked_rows: int = Field(ge=0)
    failed_rows: int = Field(ge=0)
    delivery_rows: list[dict[str, str]] = Field(default_factory=list)
    review_issues: list[BatchReviewIssue] = Field(default_factory=list)
    evaluation_diagnostics: list[BatchEvaluationDiagnostic] = Field(default_factory=list)
    row_results: list[CatalogueEnrichmentResult] = Field(default_factory=list)


def run_catalogue_batch(
    rows_or_csv: Sequence[CatalogInputRow] | str | Path,
    delivery_schema: DeliverySchema,
    source_urls: Mapping[str, Sequence[str]] | SourceURLResolver | None = None,
    *,
    expected_delivery_rows: Mapping[str, Mapping[str, str]] | ExpectedRowResolver | None = None,
    client=None,
    provider: ManufacturerEnrichmentProvider | None = None,
    manufacturer_reference: ManufacturerReference | None = None,
    brand_reference: BrandReference | None = None,
    attribute_reference: AttributeReference | None = None,
    uom_reference: UOMReference | None = None,
    attribute_mappings: AttributeDeliveryMappings | None = None,
    row_enricher: RowEnricher | None = None,
) -> BatchResult:
    """Process catalogue rows in input order using the existing row workflow.

    ``source_urls`` may be a mapping keyed by MPN or a resolver receiving the
    complete raw row. An absent configuration supplies no URLs; the existing
    row function then produces a failed row outcome rather than allowing the
    batch to drop it.
    """
    rows = load_catalog_rows(rows_or_csv) if isinstance(rows_or_csv, (str, Path)) else list(rows_or_csv)
    enrich = row_enricher or enrich_catalogue_row
    row_results: list[CatalogueEnrichmentResult] = []
    delivery_rows: list[dict[str, str]] = []
    review_issues: list[BatchReviewIssue] = []
    evaluation_diagnostics: list[BatchEvaluationDiagnostic] = []

    for row_index, row in enumerate(rows):
        try:
            urls = _resolve_source_urls(source_urls, row)
            expected = _resolve_expected_row(expected_delivery_rows, row)
            result = enrich(
                row,
                urls,
                delivery_schema,
                client=client,
                provider=provider,
                manufacturer_reference=manufacturer_reference,
                brand_reference=brand_reference,
                attribute_reference=attribute_reference,
                uom_reference=uom_reference,
                attribute_mappings=attribute_mappings,
                expected_delivery_row=dict(expected) if expected is not None else None,
            )
        except (CatalogueEnrichmentError, RuntimeError, ValueError, OSError) as error:
            result = _failed_result(row, delivery_schema, error)

        row_results.append(result)
        delivery_rows.append(_safe_delivery_row(result, delivery_schema))
        review_issues.extend(
            BatchReviewIssue(row_index=row_index, mfg_part_num=row.Mfg_Part_Num, issue=issue)
            for issue in result.review.issues
        )
        if result.evaluation_comparison is not None:
            evaluation_diagnostics.append(
                BatchEvaluationDiagnostic(
                    row_index=row_index,
                    mfg_part_num=row.Mfg_Part_Num,
                    comparison=result.evaluation_comparison,
                )
            )

    counts = {status: sum(result.review.status == status for result in row_results) for status in (
        "ready", "needs_review", "blocked", "failed"
    )}
    return BatchResult(
        total_rows=len(rows),
        processed_rows=len(row_results),
        ready_rows=counts["ready"],
        needs_review_rows=counts["needs_review"],
        blocked_rows=counts["blocked"],
        failed_rows=counts["failed"],
        delivery_rows=delivery_rows,
        review_issues=review_issues,
        evaluation_diagnostics=evaluation_diagnostics,
        row_results=row_results,
    )


def _resolve_source_urls(
    resolver: Mapping[str, Sequence[str]] | SourceURLResolver | None,
    row: CatalogInputRow,
) -> list[str]:
    if resolver is None:
        return []
    values = resolver(row) if callable(resolver) else resolver.get(row.Mfg_Part_Num, ())
    return list(values)


def _resolve_expected_row(
    resolver: Mapping[str, Mapping[str, str]] | ExpectedRowResolver | None,
    row: CatalogInputRow,
) -> Mapping[str, str] | None:
    if resolver is None:
        return None
    return resolver(row) if callable(resolver) else resolver.get(row.Mfg_Part_Num)


def _safe_delivery_row(
    result: CatalogueEnrichmentResult,
    schema: DeliverySchema,
) -> dict[str, str]:
    """Emit populated enrichment only for ready rows; retain raw fields otherwise."""
    raw_row = map_raw_fields_to_delivery(result.catalogue_row, schema)
    if result.review.status == "ready":
        delivery = schema.validate_row(result.delivery_row)
        for field in result.catalogue_row.raw_fields():
            delivery[field] = raw_row[field]
        return schema.validate_row(delivery)
    return raw_row


def _failed_result(
    row: CatalogInputRow,
    schema: DeliverySchema,
    error: Exception,
) -> CatalogueEnrichmentResult:
    message = str(error).strip() or "Unexpected row enrichment failure."
    issue = ReviewIssue(
        code="BATCH_ROW_FAILED",
        severity="error",
        scope="row",
        message=f"Catalogue row failed during batch enrichment: {message}",
        current_value=row.Mfg_Part_Num,
        affects_delivery=True,
    )
    return CatalogueEnrichmentResult(
        catalogue_row=row,
        pipeline_result=None,
        delivery_row=map_raw_fields_to_delivery(row, schema),
        source_diagnostics=[
            EnrichmentSourceDiagnostic(
                url="",
                success=False,
                error=message,
            )
        ],
        review=ReviewReport(status="failed", issues=[issue]),
    )
