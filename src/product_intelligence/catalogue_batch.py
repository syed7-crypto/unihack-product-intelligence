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
from .reference_data import (
    AttributeReference,
    BrandManufacturerReference,
    BrandReference,
    ManufacturerReference,
    UOMReference,
)
from .review import ReviewIssue, ReviewReport
from .source_discovery import (
    ManufacturerSourcePolicy,
    SearchProviderError,
    SerperSearchProvider,
    SourceSearchProvider,
    discover_and_verify_sources,
)
from .pilot_policies import resolve_source_policy_for_row
from .runtime_policy import (
    CandidateTelemetry,
    IdentityResolutionResult,
    RuntimeDomainCandidateProvider,
    RuntimeAuthorityVerifier,
    RuntimeSiteIdentityVerifier,
    resolve_identity_and_source_policy,
)
from .runtime_timing import (
    RuntimeTimingAccumulator,
    RuntimeTimingSummary,
    SearchTimingRecord,
)


SourceURLResolver = Callable[[CatalogInputRow], Sequence[str]]
ExpectedRowResolver = Callable[[CatalogInputRow], Mapping[str, str] | None]
RowEnricher = Callable[..., CatalogueEnrichmentResult]
DiscoveryPolicyResolver = Callable[[CatalogInputRow], ManufacturerSourcePolicy | None]


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


class BatchCandidateTelemetry(BaseModel):
    """One candidate diagnostic retained with its catalogue-row identity."""

    mfg_part_num: str
    telemetry: CandidateTelemetry


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
    candidate_telemetry: list[BatchCandidateTelemetry] = Field(default_factory=list)
    runtime_timing: RuntimeTimingSummary = Field(default_factory=RuntimeTimingSummary)
    search_telemetry: list[SearchTimingRecord] = Field(default_factory=list)


class _TimedSearchProvider:
    """Delegate search unchanged while recording aggregate call timing."""

    def __init__(
        self,
        inner: SourceSearchProvider,
        timing: RuntimeTimingAccumulator,
        mpn: str = "",
    ) -> None:
        self._inner = inner
        self._timing = timing
        self._mpn = mpn

    def for_mpn(self, mpn: str) -> "_TimedSearchProvider":
        return _TimedSearchProvider(self._inner, self._timing, mpn)

    def search(self, query: str, max_results: int):
        query_kind = (
            "domain_constrained"
            if query.lstrip().casefold().startswith("site:")
            else "initial"
        )
        started = self._timing.now()
        try:
            results = self._inner.search(query, max_results)
        except Exception as error:
            self._timing.record_search(
                mpn=self._mpn,
                query=query,
                query_kind=query_kind,
                duration_seconds=self._timing.now() - started,
                result_count=0,
                error_category=_safe_search_error_category(error),
            )
            raise
        self._timing.record_search(
            mpn=self._mpn,
            query=query,
            query_kind=query_kind,
            duration_seconds=self._timing.now() - started,
            result_count=len(results),
        )
        return results


def _safe_search_error_category(error: Exception) -> str:
    allowed = {
        "invalid_configuration", "missing_api_key", "invalid_query", "invalid_limit",
        "rate_limited", "http_error", "provider_unavailable", "malformed_response",
    }
    code = getattr(error, "code", None)
    if isinstance(error, SearchProviderError) and code in allowed:
        return code
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, OSError):
        return "connection_error"
    return "unknown_error"


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
    brand_manufacturer_reference: BrandManufacturerReference | None = None,
    attribute_reference: AttributeReference | None = None,
    uom_reference: UOMReference | None = None,
    attribute_mappings: AttributeDeliveryMappings | None = None,
    row_enricher: RowEnricher | None = None,
    discovery_enabled: bool = False,
    search_provider: SourceSearchProvider | None = None,
    discovery_policy_resolver: DiscoveryPolicyResolver | None = None,
    discovery_max_results_per_query: int = 10,
    runtime_policy_resolution_enabled: bool = False,
    runtime_authority_verifier: RuntimeAuthorityVerifier | None = None,
    runtime_candidate_domain_provider: RuntimeDomainCandidateProvider | None = None,
    runtime_site_identity_verifier: RuntimeSiteIdentityVerifier | None = None,
    runtime_max_candidate_domains: int = 3,
    runtime_max_domain_searches: int = 3,
    runtime_timing: RuntimeTimingAccumulator | None = None,
    search_concurrency: int = 3,
    attribute_extraction_concurrency: int = 1,
) -> BatchResult:
    """Process catalogue rows in input order using the existing row workflow.

    ``source_urls`` may be a mapping keyed by MPN or a resolver receiving the
    complete raw row. When ``discovery_enabled`` is true, rows without explicit
    URLs use the governed pilot policy registry and configured search provider.
    Search output is never passed to enrichment; only retrieved,
    exact-MPN-verified sources are passed onward.
    """
    rows = load_catalog_rows(rows_or_csv) if isinstance(rows_or_csv, (str, Path)) else list(rows_or_csv)
    timing = runtime_timing or RuntimeTimingAccumulator()
    batch_started = timing.now()
    timed_search_provider = (
        _TimedSearchProvider(search_provider, timing)
        if search_provider is not None else None
    )
    effective_provider = (
        provider.with_runtime_timing(timing)
        if isinstance(provider, ManufacturerEnrichmentProvider)
        else provider
    )
    enrich = row_enricher or enrich_catalogue_row
    row_results: list[CatalogueEnrichmentResult] = []
    delivery_rows: list[dict[str, str]] = []
    review_issues: list[BatchReviewIssue] = []
    evaluation_diagnostics: list[BatchEvaluationDiagnostic] = []
    candidate_telemetry: list[BatchCandidateTelemetry] = []

    for row_index, row in enumerate(rows):
        row_candidate_telemetry: list[CandidateTelemetry] = []
        row_search_provider = (
            timed_search_provider.for_mpn(row.Mfg_Part_Num)
            if timed_search_provider is not None
            else None
        )
        try:
            urls = _resolve_source_urls(source_urls, row)
            expected = _resolve_expected_row(expected_delivery_rows, row)
            if discovery_enabled and not urls:
                discovery = _discover_for_row(
                    row,
                    search_provider=row_search_provider,
                    enrichment_provider=effective_provider,
                    policy_resolver=discovery_policy_resolver,
                    manufacturer_reference=manufacturer_reference,
                    brand_reference=brand_reference,
                    max_results_per_query=discovery_max_results_per_query,
                    runtime_policy_resolution_enabled=runtime_policy_resolution_enabled,
                    runtime_authority_verifier=runtime_authority_verifier,
                    runtime_candidate_domain_provider=runtime_candidate_domain_provider,
                    runtime_site_identity_verifier=runtime_site_identity_verifier,
                    runtime_max_candidate_domains=runtime_max_candidate_domains,
                    runtime_max_domain_searches=runtime_max_domain_searches,
                    runtime_timing=timing,
                    search_concurrency=search_concurrency,
                )
                if discovery.runtime_identity is not None:
                    row_candidate_telemetry = list(
                        discovery.runtime_identity.candidate_telemetry
                    )
                result = enrich(
                    row,
                    [],
                    delivery_schema,
                    client=client,
                    provider=effective_provider,
                    manufacturer_reference=manufacturer_reference,
                    brand_reference=brand_reference,
                    brand_manufacturer_reference=brand_manufacturer_reference,
                    attribute_reference=attribute_reference,
                    uom_reference=uom_reference,
                    attribute_mappings=attribute_mappings,
                    expected_delivery_row=dict(expected) if expected is not None else None,
                    verified_sources=discovery.verified_sources,
                    initial_source_diagnostics=discovery.diagnostics,
                    runtime_identity=discovery.runtime_identity,
                    runtime_timing=timing,
                    attribute_extraction_concurrency=attribute_extraction_concurrency,
                )
            else:
                result = enrich(
                    row,
                    urls,
                    delivery_schema,
                    client=client,
                    provider=effective_provider,
                    manufacturer_reference=manufacturer_reference,
                    brand_reference=brand_reference,
                    brand_manufacturer_reference=brand_manufacturer_reference,
                    attribute_reference=attribute_reference,
                    uom_reference=uom_reference,
                    attribute_mappings=attribute_mappings,
                    expected_delivery_row=dict(expected) if expected is not None else None,
                    runtime_timing=timing,
                    attribute_extraction_concurrency=attribute_extraction_concurrency,
                )
        except (CatalogueEnrichmentError, RuntimeError, ValueError, OSError) as error:
            result = _failed_result(row, delivery_schema, error)

        result.candidate_telemetry = row_candidate_telemetry
        candidate_telemetry.extend(
            BatchCandidateTelemetry(
                mfg_part_num=row.Mfg_Part_Num,
                telemetry=telemetry,
            )
            for telemetry in row_candidate_telemetry
        )
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

    timing.set_total(timing.now() - batch_started)
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
        candidate_telemetry=candidate_telemetry,
        runtime_timing=timing.snapshot(),
        search_telemetry=timing.search_snapshot(),
    )


class _DiscoveryOutcome:
    def __init__(
        self,
        verified_sources: Sequence[Any] = (),
        diagnostics: Sequence[EnrichmentSourceDiagnostic] = (),
        runtime_identity: IdentityResolutionResult | None = None,
    ) -> None:
        self.verified_sources = list(verified_sources)
        self.diagnostics = list(diagnostics)
        self.runtime_identity = runtime_identity


def _discover_for_row(
    row: CatalogInputRow,
    *,
    search_provider: SourceSearchProvider | None,
    enrichment_provider: ManufacturerEnrichmentProvider | None,
    policy_resolver: DiscoveryPolicyResolver | None,
    manufacturer_reference: ManufacturerReference | None,
    brand_reference: BrandReference | None,
    max_results_per_query: int,
    runtime_policy_resolution_enabled: bool,
    runtime_authority_verifier: RuntimeAuthorityVerifier | None,
    runtime_candidate_domain_provider: RuntimeDomainCandidateProvider | None,
    runtime_site_identity_verifier: RuntimeSiteIdentityVerifier | None,
    runtime_max_candidate_domains: int,
    runtime_max_domain_searches: int,
    runtime_timing: RuntimeTimingAccumulator | None = None,
    search_concurrency: int = 3,
) -> _DiscoveryOutcome:
    """Run one governed discovery step and convert diagnostics for enrichment."""
    policy = (
        policy_resolver(row)
        if policy_resolver is not None
        else resolve_source_policy_for_row(
            row,
            manufacturer_reference=manufacturer_reference,
            brand_reference=brand_reference,
        )
    )
    if policy is None:
        if runtime_policy_resolution_enabled:
            runtime = resolve_identity_and_source_policy(
                row,
                search_provider=search_provider,
                enrichment_provider=enrichment_provider or ManufacturerEnrichmentProvider(runtime_timing=runtime_timing),
                manufacturer_reference=manufacturer_reference,
                brand_reference=brand_reference,
                authority_verifier=runtime_authority_verifier,
                max_results=max_results_per_query,
                candidate_domain_provider=runtime_candidate_domain_provider,
                site_identity_verifier=runtime_site_identity_verifier,
                max_candidate_domains=runtime_max_candidate_domains,
                max_domain_searches=runtime_max_domain_searches,
                runtime_timing=runtime_timing,
                search_concurrency=search_concurrency,
            )
            return _runtime_outcome(runtime)
        return _DiscoveryOutcome(
            diagnostics=[
                EnrichmentSourceDiagnostic(
                    url="",
                    success=False,
                    error="No explicit manufacturer source policy was configured for this row.",
                )
            ]
        )

    provider = enrichment_provider or ManufacturerEnrichmentProvider(runtime_timing=runtime_timing)
    search = search_provider or SerperSearchProvider.from_environment()
    try:
        verification = discover_and_verify_sources(
            row,
            policy,
            search,
            provider,
            max_results_per_query=max_results_per_query,
            search_concurrency=search_concurrency,
        )
    except Exception as error:
        return _DiscoveryOutcome(
            diagnostics=[
                EnrichmentSourceDiagnostic(
                    url="",
                    success=False,
                    error=f"Source discovery failed: {error}",
                )
            ]
        )

    diagnostics = [
        EnrichmentSourceDiagnostic(
            url=item.url,
            success=False,
            code=item.code,
            error=item.error
            or (
                "Search candidate was rejected by the approved manufacturer-domain policy."
                if item.verification_status == "rejected"
                else "Discovered source failed exact-MPN verification."
            ),
        )
        for item in verification.diagnostics
        if item.verification_status not in {"verified", "verified_secondary"}
    ]
    diagnostics.extend(
        EnrichmentSourceDiagnostic(
            url="",
            success=False,
            code="SOURCE_RETRIEVAL_FAILED",
            error=error,
        )
        for error in verification.discovery.errors
    )
    if not verification.verified_sources and not diagnostics:
        reason = (
            "No verified manufacturer source was found."
            if verification.discovery.status != "failed"
            else "Source discovery failed without a verified manufacturer source."
        )
        diagnostics.append(
            EnrichmentSourceDiagnostic(
                url="",
                success=False,
                code=verification.failure_code or "NO_TRUSTWORTHY_SOURCE",
                error=reason,
            )
        )
    return _DiscoveryOutcome(
        verification.verified_sources,
        diagnostics,
        runtime_identity=IdentityResolutionResult(
            state="known",
            resolved_identity=policy.manufacturer_name,
            identity_kind=policy.identity_kind,
            approved_domains=policy.approved_domains,
            reason="A controlled source policy supplied the verified identity for this row.",
            verified_sources=list(verification.verified_sources),
        ),
    )


def _runtime_outcome(result: IdentityResolutionResult) -> _DiscoveryOutcome:
    if result.state == "resolvable":
        return _DiscoveryOutcome(
            verified_sources=result.verified_sources,
            diagnostics=[],
            runtime_identity=result,
        )
    if result.failure_code == "SOURCE_RETRIEVAL_FAILED":
        reason = f"SOURCE_RETRIEVAL_FAILED: {result.reason}"
    else:
        reason = f"Identity/source policy resolution UNKNOWN: {result.reason}"
    if result.diagnostics:
        reason = f"{reason} {' | '.join(result.diagnostics)}"
    return _DiscoveryOutcome(
        diagnostics=[
            EnrichmentSourceDiagnostic(
                url="",
                success=False,
                code=result.failure_code or "IDENTITY_UNRESOLVED",
                error=reason,
            )
        ],
        runtime_identity=result,
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
