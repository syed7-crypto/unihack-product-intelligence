"""Ephemeral, fail-closed runtime identity and source-policy resolution."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from html.parser import HTMLParser
from typing import Literal, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .catalog_input import CatalogInputRow, brand_candidate
from .candidate_ranking import CandidateRanking, rank_candidate
from .manufacturer_enrichment import ManufacturerEnrichmentProvider, ManufacturerSource
from .pilot_policies import (
    ControlledSourcePolicy,
    get_controlled_source_policy,
    resolve_source_policy_for_row,
)
from .reference_data import (
    BrandReference,
    IdentityAssertion,
    ManufacturerReference,
    normalize_reference_value,
)
from .runtime_timing import RuntimeTimingAccumulator
from .search_parallel import search_in_order
from .source_discovery import SearchResult, SourceSearchProvider


IdentityResolutionState = Literal["known", "resolvable", "unknown"]
RuntimeIdentityKind = Literal["manufacturer", "brand"]
IdentityFailureCode = Literal[
    "SOURCE_RETRIEVAL_FAILED",
    "NO_TRUSTWORTHY_SOURCE",
    "MANUFACTURER_IDENTITY_CONFLICT",
]
MAX_GOVERNED_DISCOVERY_DOMAINS = 3


class RuntimeAuthorityEvidence(BaseModel):
    """Explicit caller-supplied authority attestation for a candidate domain."""

    controlled_identity: str = Field(min_length=1)
    identity_kind: RuntimeIdentityKind
    domain: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence_reference: str | None = None


class RuntimeAuthorityVerifier(Protocol):
    def __call__(
        self, row: CatalogInputRow, candidate: SearchResult
    ) -> RuntimeAuthorityEvidence | None:
        """Return evidence only when the candidate is independently verified."""


class RuntimeDomainCandidate(BaseModel):
    """Untrusted domain proposal; it is never a policy or evidence."""

    domain: str = Field(min_length=1)
    identity_hint: str | None = None
    discovery_url: str | None = None
    discovery_query: str | None = None
    discovery_rank: int = Field(default=1, ge=1)
    ranking: CandidateRanking | None = None


class RuntimeDomainCandidateProvider(Protocol):
    def __call__(self, row: CatalogInputRow) -> Sequence[RuntimeDomainCandidate]:
        """Return untrusted candidate domains for one row."""


class RuntimeSiteIdentityVerifier(Protocol):
    def __call__(
        self,
        row: CatalogInputRow,
        candidate: RuntimeDomainCandidate,
        source: ManufacturerSource,
        extracted_text: str,
    ) -> RuntimeAuthorityEvidence | None:
        """Verify identity from the retrieved manufacturer-site content."""


class CandidateTelemetry(BaseModel):
    """Bounded diagnostic record for one considered source candidate."""

    url: str = ""
    domain: str = ""
    query: str | None = None
    ranking: CandidateRanking | None = None
    fetched: bool = False
    http_status: int | None = None
    content_type: str | None = None
    exact_mpn_verified: bool | None = None
    identity_value: str | None = None
    identity_kind: RuntimeIdentityKind | None = None
    identity_result: str | None = None
    rejection_code: str | None = None


class IdentityResolutionResult(BaseModel):
    """Outcome of controlled identity/source resolution for one row."""

    state: IdentityResolutionState
    resolved_identity: str | None = None
    identity_kind: RuntimeIdentityKind | None = None
    approved_domains: tuple[str, ...] = ()
    reason: str
    diagnostics: list[str] = Field(default_factory=list)
    failure_code: IdentityFailureCode | None = None
    runtime_policy: ControlledSourcePolicy | None = None
    verified_sources: list[ManufacturerSource] = Field(default_factory=list)
    selected_ranking: CandidateRanking | None = None
    identity_assertion: IdentityAssertion | None = None
    candidate_telemetry: list[CandidateTelemetry] = Field(default_factory=list)


def _identity_assertion(
    value: str,
    kind: RuntimeIdentityKind,
    source: str,
    evidence_reference: str | None = None,
) -> IdentityAssertion:
    return IdentityAssertion(
        value=value,
        kind=kind,
        source=source,
        trust_level="high" if source != "catalogue" else "low",
        evidence_reference=evidence_reference,
    )


def resolve_identity_and_source_policy(
    row: CatalogInputRow,
    *,
    search_provider: SourceSearchProvider | None = None,
    enrichment_provider: ManufacturerEnrichmentProvider | None = None,
    manufacturer_reference: ManufacturerReference | None = None,
    brand_reference: BrandReference | None = None,
    authority_verifier: RuntimeAuthorityVerifier | None = None,
    candidate_domain_provider: RuntimeDomainCandidateProvider | None = None,
    site_identity_verifier: RuntimeSiteIdentityVerifier | None = None,
    max_results: int = 10,
    max_candidate_domains: int = 3,
    max_domain_searches: int = 3,
    max_source_attempts: int = 3,
    runtime_timing: RuntimeTimingAccumulator | None = None,
    search_concurrency: int = 3,
) -> IdentityResolutionResult:
    """Resolve known identities or safely attempt one ephemeral runtime policy.

    Search results are never authority evidence. In the default product-first
    path, candidate domains are searched in a domain-constrained way and the
    retrieved page must provide exact-MPN and site-identity evidence before a
    policy is created. An injected deterministic verifier remains available as
    a strict compatibility/test seam. Successful runtime policies are returned
    only in this result and are never written to the controlled registry.
    """
    known = resolve_source_policy_for_row(
        row,
        manufacturer_reference=manufacturer_reference,
        brand_reference=brand_reference,
    )
    if known is not None:
        return IdentityResolutionResult(
            state="known",
            resolved_identity=known.manufacturer_name,
            identity_kind=known.identity_kind,
            approved_domains=known.approved_domains,
            reason="A controlled manufacturer/brand policy was resolved.",
            identity_assertion=_identity_assertion(
                known.manufacturer_name,
                known.identity_kind,
                "controlled_reference",
            ),
        )

    if search_provider is None or enrichment_provider is None:
        return IdentityResolutionResult(
            state="unknown",
            reason="No controlled identity or runtime authority verifier was available.",
            failure_code="NO_TRUSTWORTHY_SOURCE",
        )
    if max_results < 1:
        raise ValueError("max_results must be positive.")

    if max_candidate_domains < 1 or max_domain_searches < 1 or max_source_attempts < 1:
        raise ValueError("Runtime candidate/search limits must be positive.")
    if search_concurrency < 1:
        raise ValueError("search concurrency must be positive.")

    # Preserve the original injected-verifier contract as a strict compatibility
    # path. The product-first path below verifies identity after retrieving the
    # actual candidate-domain page.
    if authority_verifier is not None:
        return _resolve_with_legacy_authority_verifier(
            row,
            search_provider=search_provider,
            enrichment_provider=enrichment_provider,
            authority_verifier=authority_verifier,
            manufacturer_reference=manufacturer_reference,
            brand_reference=brand_reference,
            max_results=max_results,
            max_source_attempts=max_source_attempts,
        )

    diagnostics: list[str] = []
    try:
        if candidate_domain_provider is not None:
            domain_candidates = list(candidate_domain_provider(row))[:max_candidate_domains]
        else:
            search_results: list[tuple[SearchResult, str]] = []
            search_errors: list[str] = []
            queries = _runtime_discovery_queries(
                row,
                manufacturer_reference=manufacturer_reference,
                brand_reference=brand_reference,
            )
            outcomes = search_in_order(
                search_provider,
                queries,
                max_results,
                concurrency=search_concurrency,
            )
            for query, outcome in zip(queries, outcomes):
                if outcome.error is not None:
                    error = outcome.error
                    search_errors.append(f"Search failed for query '{query}': {error}")
                    continue
                search_results.extend(
                    (result, query) for result in outcome.results
                )
            diagnostics.extend(search_errors)
            if not search_results and search_errors:
                raise RuntimeError(search_errors[-1])
            ranked_results = []
            expected_identity = _catalogue_identity_hint(
                row,
                manufacturer_reference=manufacturer_reference,
                brand_reference=brand_reference,
            )
            for result, query in search_results:
                ranking = rank_candidate(
                    url=result.url,
                    title=result.title,
                    snippet=result.snippet,
                    expected_mpn=row.Mfg_Part_Num,
                    expected_identities=(expected_identity,) if expected_identity else (),
                    expected_description=row.Part_Desc,
                    approved_domain=False,
                    exact_mpn_in_result=_contains_runtime_mpn(result, row.Mfg_Part_Num),
                )
                ranked_results.append((result, ranking, query))
            # Python's stable sort preserves the existing search/provider order
            # for equal scores; query expansion does not alter ranking ties.
            ranked_results.sort(key=lambda item: -item[1].score)
            domain_candidates = _domain_candidates_from_search(
                ranked_results, max_candidate_domains
            )
    except Exception as error:
        return IdentityResolutionResult(
            state="unknown",
            reason="Runtime candidate-domain discovery failed; no policy was created.",
            diagnostics=[str(error) or "Search failed."],
            failure_code="SOURCE_RETRIEVAL_FAILED",
        )

    retrieval_failure_seen = False
    attempted_sources = 0
    candidate_telemetry: list[CandidateTelemetry] = []
    domain_queues: list[tuple[RuntimeDomainCandidate, str, list[str], str]] = []
    pending_domain_searches: list[tuple[RuntimeDomainCandidate, str, str]] = []
    for domain_candidate in domain_candidates:
        domain = _normalize_candidate_domain(domain_candidate.domain)
        if domain is None:
            candidate_telemetry.append(
                CandidateTelemetry(
                    url=domain_candidate.discovery_url or "",
                    domain=domain_candidate.domain,
                    query=domain_candidate.discovery_query,
                    ranking=domain_candidate.ranking,
                    rejection_code="INVALID_HTTPS_CANDIDATE",
                    identity_result="not_considered",
                )
            )
            diagnostics.append("Candidate rejected because it is not an HTTPS URL.")
            continue
        if _is_retailer_domain(domain):
            candidate_telemetry.append(
                CandidateTelemetry(
                    url=domain_candidate.discovery_url or "",
                    domain=domain,
                    query=domain_candidate.discovery_query,
                    ranking=domain_candidate.ranking,
                    rejection_code="RETAILER_DOMAIN_REJECTED",
                    identity_result="not_considered",
                )
            )
            diagnostics.append(f"Candidate retailer domain rejected: {domain}.")
            continue
        if domain_candidate.ranking is not None and domain_candidate.ranking.decision == "bad":
            candidate_telemetry.append(
                CandidateTelemetry(
                    url=domain_candidate.discovery_url or "",
                    domain=domain,
                    query=domain_candidate.discovery_query,
                    ranking=domain_candidate.ranking,
                    rejection_code="CANDIDATE_RANKING_BAD",
                    identity_result="not_considered",
                )
            )
            diagnostics.append(
                "Candidate skipped by deterministic ranking: "
                + "; ".join(domain_candidate.ranking.reasons)
            )
            continue
        domain_query = f'site:{domain} "{row.Mfg_Part_Num.strip()}"'
        pending_domain_searches.append((domain_candidate, domain, domain_query))

    domain_outcomes = search_in_order(
        search_provider,
        [item[2] for item in pending_domain_searches],
        max_domain_searches,
        concurrency=search_concurrency,
    )
    for (domain_candidate, domain, domain_query), outcome in zip(
        pending_domain_searches, domain_outcomes
    ):
        if outcome.error is not None:
            error = outcome.error
            retrieval_failure_seen = True
            candidate_telemetry.append(
                CandidateTelemetry(
                    url=domain_candidate.discovery_url or "",
                    domain=domain,
                    query=domain_query,
                    ranking=domain_candidate.ranking,
                    rejection_code="DOMAIN_SEARCH_FAILED",
                    identity_result="not_considered",
                )
            )
            diagnostics.append(f"Domain-constrained search failed for {domain}: {error}")
            continue

        candidate_urls = _candidate_urls_for_domain(outcome.results, domain)
        if domain_candidate.discovery_url:
            candidate_urls.insert(0, domain_candidate.discovery_url)
        unique_urls = list(dict.fromkeys(candidate_urls))
        if unique_urls:
            domain_queues.append((domain_candidate, domain, unique_urls, domain_query))

    # Schedule one URL per domain per round. This preserves the global attempt
    # limit while preventing one domain's result list from consuming it all.
    scheduled_candidates: list[tuple[RuntimeDomainCandidate, str, str, str]] = []
    while any(queue[2] for queue in domain_queues):
        for domain_candidate, domain, urls, domain_query in domain_queues:
            if urls:
                scheduled_candidates.append((domain_candidate, domain, urls.pop(0), domain_query))

    for domain_candidate, domain, url, domain_query in scheduled_candidates:
        if attempted_sources >= max_source_attempts:
            candidate_telemetry.append(
                CandidateTelemetry(
                    url=url,
                    domain=domain,
                    query=domain_query,
                    ranking=domain_candidate.ranking,
                    rejection_code="ATTEMPT_LIMIT_REACHED",
                    identity_result="not_fetched",
                )
            )
            diagnostics.append(
                f"Candidate retrieval attempt limit ({max_source_attempts}) reached; "
                "remaining URLs were not fetched."
            )
            continue
        attempted_sources += 1
        source, evidence, transport_failed = _verify_runtime_candidate(
            row,
            domain_candidate=domain_candidate,
            domain=domain,
            url=url,
            query=domain_query,
            enrichment_provider=enrichment_provider,
            site_identity_verifier=site_identity_verifier,
            manufacturer_reference=manufacturer_reference,
            brand_reference=brand_reference,
            candidate_telemetry=candidate_telemetry,
            diagnostics=diagnostics,
        )
        retrieval_failure_seen = retrieval_failure_seen or transport_failed
        if source is None or evidence is None:
            continue
        evidence_domain = normalize_reference_value(evidence.domain).rstrip(".")
        if evidence_domain != domain:
            continue
        runtime_policy = ControlledSourcePolicy(
            controlled_identity=evidence.controlled_identity,
            identity_kind=evidence.identity_kind,
            approved_domains=(domain,),
            governance_reason=evidence.reason,
        )
        return IdentityResolutionResult(
            state="resolvable",
            resolved_identity=evidence.controlled_identity,
            identity_kind=evidence.identity_kind,
            approved_domains=(domain,),
            reason="Product existence and site identity verification succeeded.",
            diagnostics=diagnostics,
            runtime_policy=runtime_policy,
            verified_sources=[source],
            selected_ranking=domain_candidate.ranking,
            identity_assertion=_identity_assertion(
                evidence.controlled_identity,
                evidence.identity_kind,
                "page_evidence",
                evidence.evidence_reference or source.url,
            ),
            candidate_telemetry=candidate_telemetry,
        )

    return IdentityResolutionResult(
        state="unknown",
        reason="No candidate satisfied authority and exact-MPN verification.",
        diagnostics=diagnostics,
        failure_code=(
            "MANUFACTURER_IDENTITY_CONFLICT"
            if any("Catalogue identity conflicts" in item for item in diagnostics)
            else (
            "SOURCE_RETRIEVAL_FAILED"
            if retrieval_failure_seen
            else "NO_TRUSTWORTHY_SOURCE"
            )
        ),
        candidate_telemetry=candidate_telemetry,
    )


def _verify_runtime_candidate(
    row: CatalogInputRow,
    *,
    domain_candidate: RuntimeDomainCandidate,
    domain: str,
    url: str,
    query: str,
    enrichment_provider: ManufacturerEnrichmentProvider,
    site_identity_verifier: RuntimeSiteIdentityVerifier | None,
    manufacturer_reference: ManufacturerReference | None,
    brand_reference: BrandReference | None,
    candidate_telemetry: list[CandidateTelemetry],
    diagnostics: list[str],
) -> tuple[ManufacturerSource | None, RuntimeAuthorityEvidence | None, bool]:
    """Verify one scheduled candidate without changing acceptance rules."""
    telemetry = CandidateTelemetry(
        url=url,
        domain=domain,
        query=query,
        ranking=domain_candidate.ranking,
        fetched=True,
    )
    candidate_telemetry.append(telemetry)
    scoped_provider = enrichment_provider.with_approved_domains({domain})
    retrieval = scoped_provider.retrieve_source(
        url,
        row.Mfg_Part_Num,
        expected_identity=_catalogue_identity_hint(
            row,
            manufacturer_reference=manufacturer_reference,
            brand_reference=brand_reference,
        ),
        expected_description=row.Part_Desc,
    )
    if not retrieval.success or retrieval.source is None:
        telemetry.http_status = retrieval.http_status
        telemetry.content_type = retrieval.content_type
        telemetry.exact_mpn_verified = False
        telemetry.identity_result = "not_evaluated"
        telemetry.rejection_code = retrieval.code or "SOURCE_RETRIEVAL_FAILED"
        error_text = retrieval.error or "Exact-MPN source verification failed."
        diagnostics.append(error_text)
        return None, None, _is_transport_retrieval_failure(error_text)

    source = retrieval.source
    telemetry.http_status = retrieval.http_status
    telemetry.content_type = retrieval.content_type
    telemetry.exact_mpn_verified = source.exact_mpn_verified
    try:
        normalized = scoped_provider.to_normalized_source(source)
        evidence = (
            site_identity_verifier(row, domain_candidate, source, normalized.extracted_text)
            if site_identity_verifier is not None
            else _deterministic_site_identity(
                domain_candidate,
                source,
                normalized.extracted_text,
                expected_identity=_catalogue_identity_hint(
                    row,
                    manufacturer_reference=manufacturer_reference,
                    brand_reference=brand_reference,
                ),
            )
        )
    except Exception as error:
        telemetry.identity_result = "error"
        telemetry.rejection_code = "IDENTITY_VERIFICATION_FAILED"
        diagnostics.append(f"Site identity verification failed: {error}")
        return None, None, False

    if evidence is None:
        telemetry.identity_result = "not_verified"
        telemetry.rejection_code = "IDENTITY_NOT_VERIFIED"
        diagnostics.append("Candidate site did not provide matching manufacturer identity evidence.")
        return None, None, False

    telemetry.identity_value = evidence.controlled_identity
    telemetry.identity_kind = evidence.identity_kind
    evidence_domain = normalize_reference_value(evidence.domain).rstrip(".")
    if evidence_domain != domain:
        telemetry.identity_result = "domain_mismatch"
        telemetry.rejection_code = "IDENTITY_DOMAIN_MISMATCH"
        diagnostics.append("Site identity evidence domain did not match the candidate domain.")
        return None, None, False

    conflict = _catalogue_identity_conflict(
        row,
        evidence.controlled_identity,
        discovered_kind=evidence.identity_kind,
        manufacturer_reference=manufacturer_reference,
        brand_reference=brand_reference,
    )
    if conflict is not None:
        telemetry.identity_result = "conflict"
        telemetry.rejection_code = "MANUFACTURER_IDENTITY_CONFLICT"
        diagnostics.append(conflict)
        return None, None, False

    telemetry.identity_result = "verified"
    return source, evidence, False


def _resolve_with_legacy_authority_verifier(
    row: CatalogInputRow,
    *,
    search_provider: SourceSearchProvider,
    enrichment_provider: ManufacturerEnrichmentProvider,
    authority_verifier: RuntimeAuthorityVerifier,
    manufacturer_reference: ManufacturerReference | None,
    brand_reference: BrandReference | None,
    max_results: int,
    max_source_attempts: int,
) -> IdentityResolutionResult:
    diagnostics: list[str] = []
    candidate_telemetry: list[CandidateTelemetry] = []
    try:
        search_results = search_provider.search(row.Mfg_Part_Num.strip(), max_results)
    except Exception as error:
        return IdentityResolutionResult(
            state="unknown", reason="Runtime identity search failed; no policy was created.",
            diagnostics=[str(error) or "Search failed."],
            failure_code="SOURCE_RETRIEVAL_FAILED",
        )
    attempted_sources = 0
    for candidate in search_results:
        parsed = urlparse(candidate.url)
        domain = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme.casefold() != "https" or not domain or _is_retailer_domain(domain):
            candidate_telemetry.append(CandidateTelemetry(
                url=candidate.url,
                domain=domain,
                query=row.Mfg_Part_Num.strip(),
                rejection_code="CANDIDATE_GOVERNANCE_REJECTED",
                identity_result="not_considered",
            ))
            diagnostics.append("Candidate rejected by HTTPS/retailer safety checks.")
            continue
        evidence = authority_verifier(row, candidate)
        if evidence is None or normalize_reference_value(evidence.domain).rstrip(".") != domain:
            candidate_telemetry.append(CandidateTelemetry(
                url=candidate.url,
                domain=domain,
                query=row.Mfg_Part_Num.strip(),
                identity_result="not_verified",
                rejection_code="IDENTITY_AUTHORITY_NOT_VERIFIED",
            ))
            diagnostics.append("Candidate had no matching independent authority evidence.")
            continue
        conflict = _catalogue_identity_conflict(
            row,
            evidence.controlled_identity,
            discovered_kind=evidence.identity_kind,
            manufacturer_reference=manufacturer_reference,
            brand_reference=brand_reference,
        )
        if conflict is not None:
            candidate_telemetry.append(CandidateTelemetry(
                url=candidate.url,
                domain=domain,
                query=row.Mfg_Part_Num.strip(),
                identity_value=evidence.controlled_identity,
                identity_kind=evidence.identity_kind,
                identity_result="conflict",
                rejection_code="MANUFACTURER_IDENTITY_CONFLICT",
            ))
            diagnostics.append(conflict)
            continue
        if attempted_sources >= max_source_attempts:
            candidate_telemetry.append(CandidateTelemetry(
                url=candidate.url,
                domain=domain,
                query=row.Mfg_Part_Num.strip(),
                rejection_code="ATTEMPT_LIMIT_REACHED",
                identity_result="not_fetched",
            ))
            diagnostics.append(
                f"Candidate retrieval attempt limit ({max_source_attempts}) reached; "
                "remaining candidates were not fetched."
            )
            break
        attempted_sources += 1
        telemetry = CandidateTelemetry(
            url=candidate.url,
            domain=domain,
            query=row.Mfg_Part_Num.strip(),
            fetched=True,
            identity_value=evidence.controlled_identity,
            identity_kind=evidence.identity_kind,
        )
        candidate_telemetry.append(telemetry)
        scoped_provider = enrichment_provider.with_approved_domains({domain})
        retrieval = scoped_provider.retrieve_source(
            candidate.url,
            row.Mfg_Part_Num,
            expected_identity=_catalogue_identity_hint(
                row,
                manufacturer_reference=manufacturer_reference,
                brand_reference=brand_reference,
            ),
            expected_description=row.Part_Desc,
        )
        if not retrieval.success or retrieval.source is None:
            telemetry.http_status = retrieval.http_status
            telemetry.content_type = retrieval.content_type
            telemetry.exact_mpn_verified = False
            telemetry.identity_result = "not_evaluated"
            telemetry.rejection_code = retrieval.code or "SOURCE_RETRIEVAL_FAILED"
            diagnostics.append(retrieval.error or "Exact-MPN source verification failed.")
            continue
        telemetry.http_status = retrieval.http_status
        telemetry.content_type = retrieval.content_type
        telemetry.exact_mpn_verified = retrieval.source.exact_mpn_verified
        telemetry.identity_result = "verified"
        runtime_policy = ControlledSourcePolicy(
            controlled_identity=evidence.controlled_identity,
            identity_kind=evidence.identity_kind,
            approved_domains=(domain,),
            governance_reason=evidence.reason,
        )
        return IdentityResolutionResult(
            state="resolvable", resolved_identity=evidence.controlled_identity,
            identity_kind=evidence.identity_kind, approved_domains=(domain,),
            reason="Authority evidence and exact-MPN source verification succeeded.",
            diagnostics=diagnostics, runtime_policy=runtime_policy,
            verified_sources=[retrieval.source],
            identity_assertion=_identity_assertion(
                evidence.controlled_identity,
                evidence.identity_kind,
                "page_evidence",
                evidence.evidence_reference or retrieval.source.url,
            ),
            candidate_telemetry=candidate_telemetry,
        )
    return IdentityResolutionResult(
        state="unknown", reason="No candidate satisfied authority and exact-MPN verification.",
        diagnostics=diagnostics,
        failure_code=(
            "MANUFACTURER_IDENTITY_CONFLICT"
            if any("Catalogue identity conflicts" in item for item in diagnostics)
            else "NO_TRUSTWORTHY_SOURCE"
        ),
        candidate_telemetry=candidate_telemetry,
    )


def _is_transport_retrieval_failure(error: str) -> bool:
    """Recognize transport failures without treating verification failures as transport.

    Exact-MPN mismatches, retailer rejection, and failed site-identity checks
    are trustworthy-source decisions, not network failures.  This small
    classifier is intentionally conservative and only covers errors produced
    by the existing search/retrieval boundaries.
    """
    lowered = error.casefold()
    transport_markers = (
        "dns",
        "name or service not known",
        "getaddrinfo failed",
        "socket",
        "timed out",
        "timeout",
        "connection refused",
        "connection reset",
        "network is unreachable",
        "temporary failure in name resolution",
        "http status",
        "source returned http",
        "quota",
        "rate limit",
        "provider unavailable",
        "retrieval failed",
    )
    verification_markers = (
        "exact mpn was not found",
        "exact mpn verification failed",
        "exact part",
    )
    if any(marker in lowered for marker in verification_markers):
        return False
    return any(marker in lowered for marker in transport_markers)


def _domain_candidates_from_search(
    results: Sequence[tuple[SearchResult, CandidateRanking, str]] | Sequence[SearchResult],
    max_candidate_domains: int,
) -> list[RuntimeDomainCandidate]:
    candidates: list[RuntimeDomainCandidate] = []
    seen: set[str] = set()
    for rank, item in enumerate(results, start=1):
        if isinstance(item, tuple):
            result, ranking, query = item
        else:
            result, ranking, query = item, None, None
        parsed = urlparse(result.url)
        domain = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme.casefold() != "https" or not domain or domain in seen:
            continue
        seen.add(domain)
        hint = domain.removeprefix("www.").split(".", 1)[0].replace("-", " ")
        candidates.append(RuntimeDomainCandidate(
            domain=domain,
            identity_hint=hint,
            discovery_url=None,
            discovery_query=query,
            discovery_rank=rank,
            ranking=ranking,
        ))
        if len(candidates) >= max_candidate_domains:
            break
    return candidates


def _contains_runtime_mpn(result: SearchResult, expected_mpn: str) -> bool:
    expected = normalize_reference_value(expected_mpn)
    return expected in normalize_reference_value(
        f"{result.title} {result.snippet} {result.url}"
    )


def _runtime_discovery_queries(
    row: CatalogInputRow,
    *,
    manufacturer_reference: ManufacturerReference | None = None,
    brand_reference: BrandReference | None = None,
) -> list[str]:
    """Build identity-aware initial queries for rows without a policy.

    The MPN remains the final fallback.  Manufacturer/brand values are query
    hints only; they do not approve domains or sources.
    """
    part_number = row.Mfg_Part_Num.strip()
    identities: list[str] = []

    raw_manufacturer = row.Part_Manuf.strip()
    if raw_manufacturer:
        # Catalogue codes in parentheses are not manufacturer identity and
        # only make search recall worse.
        manufacturer = re.sub(r"\s*\([^)]*\)\s*$", "", raw_manufacturer).strip()
        if manufacturer:
            identities.append(manufacturer)

    for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand"):
        brand = brand_candidate(getattr(row, field))
        if brand:
            identities.append(brand)

    queries: list[str] = []
    seen: set[str] = set()

    def add_query(query: str) -> None:
        normalized = normalize_reference_value(query)
        if query and normalized not in seen:
            seen.add(normalized)
            queries.append(query)

    for identity in (*identities, ""):
        query = " ".join(part for part in (part_number, identity) if part).strip()
        add_query(query)

    governed_domains = _controlled_catalogue_identity_domains(
        row,
        manufacturer_reference=manufacturer_reference,
        brand_reference=brand_reference,
    )
    for domain in governed_domains:
        add_query(f'site:{domain} "{part_number}"')

    # One bounded ecosystem/distributor variant improves recall for catalogue
    # rows whose useful source may be a governed secondary channel. It is a
    # discovery hint only; every result still passes the existing gates.
    if not governed_domains:
        brand = next(
            (
                brand_candidate(getattr(row, field))
                for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand")
                if brand_candidate(getattr(row, field))
            ),
            None,
        )
        if brand:
            add_query(f"{part_number} {brand} distributor")

    if not _has_strong_catalogue_query_identity(
        row,
        manufacturer_reference=manufacturer_reference,
        brand_reference=brand_reference,
    ):
        for suffix in ("manufacturer", "product"):
            add_query(f"{part_number} {suffix}")
    return queries


def _controlled_catalogue_identity_domains(
    row: CatalogInputRow,
    *,
    manufacturer_reference: ManufacturerReference | None = None,
    brand_reference: BrandReference | None = None,
) -> tuple[str, ...]:
    """Return domains only from existing controlled identity policies.

    Raw catalogue values are never converted into domains. A raw value can
    contribute here only when it exactly resolves to an identity already
    present in the controlled policy registry; the returned domains are used
    for discovery hints and do not authorize a source.
    """
    identities: list[tuple[str, RuntimeIdentityKind]] = []

    if manufacturer_reference is not None:
        resolved = manufacturer_reference.resolve(row.Part_Manuf)
        if resolved.status == "resolved" and isinstance(resolved.resolved_value, str):
            identities.append((resolved.resolved_value, "manufacturer"))

    if brand_reference is not None:
        for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand"):
            candidate = brand_candidate(getattr(row, field))
            if not candidate:
                continue
            resolved = brand_reference.resolve(candidate)
            if resolved.status == "resolved" and isinstance(resolved.resolved_value, str):
                identities.append((resolved.resolved_value, "brand"))

    # This is deliberately an exact lookup against controlled policy data, not
    # a conversion of arbitrary text into a domain.
    for field, kind in (
        ("Part_Manuf", "manufacturer"),
        ("E1_Brand", "brand"),
        ("Unilog_Brand", "brand"),
        ("DIB_Brand", "brand"),
    ):
        candidate = (
            re.sub(r"\s*\([^)]*\)\s*$", "", getattr(row, field)).strip()
            if field == "Part_Manuf"
            else brand_candidate(getattr(row, field))
        )
        if candidate:
            identities.append((candidate, kind))

    domains: list[str] = []
    for identity, kind in identities:
        policy = get_controlled_source_policy(identity, kind)
        if policy is None:
            continue
        for domain in policy.approved_domains:
            if domain not in domains:
                domains.append(domain)
    return tuple(domains[:MAX_GOVERNED_DISCOVERY_DOMAINS])


def _has_strong_catalogue_query_identity(
    row: CatalogInputRow,
    *,
    manufacturer_reference: ManufacturerReference | None = None,
    brand_reference: BrandReference | None = None,
) -> bool:
    """Avoid generic expansion when a controlled or catalogue brand is usable."""
    if manufacturer_reference is not None:
        result = manufacturer_reference.resolve(row.Part_Manuf)
        if result.status == "resolved":
            return True
    if brand_reference is not None:
        for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand"):
            candidate = brand_candidate(getattr(row, field))
            if candidate and brand_reference.resolve(candidate).status == "resolved":
                return True
    return any(
        brand_candidate(getattr(row, field))
        for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand")
    )


def _normalize_candidate_domain(value: str) -> str | None:
    raw = value.strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    domain = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme.casefold() != "https" or not domain:
        return None
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    return domain


def _candidate_urls_for_domain(results: Sequence[SearchResult], domain: str) -> list[str]:
    urls: list[str] = []
    for result in results:
        parsed = urlparse(result.url)
        result_domain = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme.casefold() == "https" and (
            result_domain == domain or result_domain.endswith("." + domain)
        ):
            urls.append(result.url)
    return urls


_PART_MANUF_QUALIFIERS = frozenset(
    {
        "accessory", "co", "company", "corp", "corporation", "inc",
        "incorporated", "lighting", "manufacturing", "usa",
    }
)


def _part_manufacturer_identity_candidates(value: str) -> tuple[str, ...]:
    """Return bounded, untrusted identity hints from a raw manufacturer field."""
    base = re.sub(r"\s*\([^)]*\)\s*$", "", value.strip()).strip()
    if not base:
        return ()
    candidates: list[str] = []
    current = base.split()
    while current:
        candidate = " ".join(current).strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)
        if len(current) <= 1 or current[-1].casefold().rstrip(".,") not in _PART_MANUF_QUALIFIERS:
            break
        current.pop()
    return tuple(reversed(candidates))


def _catalogue_identity_hint(
    row: CatalogInputRow,
    *,
    manufacturer_reference: ManufacturerReference | None = None,
    brand_reference: BrandReference | None = None,
) -> str | None:
    """Return the highest-priority catalogue identity hint.

    Hints never approve a source; existing page-local identity, exact-MPN,
    product-level, HTTPS, and governance gates remain mandatory.
    """
    if manufacturer_reference is not None:
        result = manufacturer_reference.resolve(row.Part_Manuf)
        if result.status == "resolved" and isinstance(result.resolved_value, str):
            return result.resolved_value
    if brand_reference is not None:
        for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand"):
            candidate = brand_candidate(getattr(row, field))
            if candidate:
                result = brand_reference.resolve(candidate)
                if result.status == "resolved" and isinstance(result.resolved_value, str):
                    return result.resolved_value
    for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand"):
        candidate = brand_candidate(getattr(row, field))
        if candidate:
            return candidate
    parsed = tuple(
        candidate
        for candidate in _part_manufacturer_identity_candidates(row.Part_Manuf)
        if not _is_non_identity_manufacturer_hint(candidate)
    )
    return parsed[0] if parsed else None


def _catalogue_identity_candidates(row: CatalogInputRow) -> tuple[str, ...]:
    """Collect catalogue identity signals without changing the raw row."""
    candidates: list[str] = []
    for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand"):
        candidate = brand_candidate(getattr(row, field))
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    for manufacturer in _part_manufacturer_identity_candidates(row.Part_Manuf):
        if (
            manufacturer
            and not _is_non_identity_manufacturer_hint(manufacturer)
            and manufacturer not in candidates
        ):
            candidates.append(manufacturer)
    return tuple(candidates)


def _is_non_identity_manufacturer_hint(value: str) -> bool:
    """Exclude explicit placeholders/distributor labels from conflict checks."""
    normalized = normalize_reference_value(value)
    return normalized in {"unknown", "unknown distributor", "n a", "na"} or "distributor" in normalized


def _catalogue_identity_conflict(
    row: CatalogInputRow,
    discovered_identity: str,
    *,
    discovered_kind: RuntimeIdentityKind = "manufacturer",
    manufacturer_reference: ManufacturerReference | None = None,
    brand_reference: BrandReference | None = None,
) -> str | None:
    """Return a review diagnostic for a clearly different discovered identity.

    Catalogue identity is never overwritten.  Matching is deterministic and
    deliberately conservative: normalized/compact equality or shared
    meaningful tokens is accepted; otherwise the candidate is kept out of the
    verified-source path and surfaced for manufacturer review.
    """
    discovered = discovered_identity.strip()
    expected = list(_catalogue_identity_candidates(row))
    if discovered_kind == "brand":
        expected = [
            candidate
            for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand")
            for candidate in [brand_candidate(getattr(row, field))]
            if candidate
        ]
    if discovered_kind == "brand":
        resolved_hint = None
        if brand_reference is not None:
            for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand"):
                candidate = brand_candidate(getattr(row, field))
                if candidate:
                    resolved = brand_reference.resolve(candidate)
                    if resolved.status == "resolved" and isinstance(resolved.resolved_value, str):
                        resolved_hint = resolved.resolved_value
                        break
    else:
        resolved_hint = _catalogue_identity_hint(
            row,
            manufacturer_reference=manufacturer_reference,
            brand_reference=brand_reference,
        )
    if resolved_hint and resolved_hint not in expected:
        expected.insert(0, resolved_hint)
    if not discovered or not expected:
        return None
    discovered_normalized = normalize_reference_value(discovered)
    discovered_compact = _compact_identity(discovered_normalized)
    discovered_tokens = {
        token for token in discovered_normalized.split() if len(token) > 2
    }
    for candidate in expected:
        normalized = normalize_reference_value(candidate)
        if normalized == discovered_normalized or _compact_identity(normalized) == discovered_compact:
            return None
        tokens = {token for token in normalized.split() if len(token) > 2}
        if discovered_tokens & tokens:
            return None
    return (
        "Catalogue identity conflicts with discovered source identity: "
        f"catalogue={expected!r}, discovered={discovered!r}."
    )


def _is_retailer_domain(domain: str) -> bool:
    retailer_roots = {
        "amazon.com", "ebay.com", "grainger.com", "homedepot.com",
        "lowes.com", "walmart.com", "costco.com", "wayfair.com",
    }
    return any(domain == root or domain.endswith("." + root) for root in retailer_roots)


def _deterministic_site_identity(
    candidate: RuntimeDomainCandidate,
    source: ManufacturerSource,
    extracted_text: str,
    *,
    expected_identity: str | None = None,
) -> RuntimeAuthorityEvidence | None:
    """Resolve identity from coherent page-local signals only.

    A hostname, URL, search result, or ``candidate.identity_hint`` is never
    an identity signal. Explicitly labelled page identity remains sufficient;
    otherwise a catalogue identity must occur in at least two independent
    page-local channels such as title, OpenGraph metadata, or visible body
    text. Explicit conflicting labels take precedence over signal counts.
    """
    page_identities = _extract_labeled_page_identities(extracted_text)
    page_identities.extend(_extract_structured_page_identities(source))
    if len({(_compact_identity(value), kind) for value, kind in page_identities}) > 1:
        return None
    if page_identities:
        discovered_identity, identity_kind = page_identities[0]
        return _runtime_identity_evidence(
            source,
            discovered_identity,
            "explicit page identity label" if identity_kind in {"manufacturer", "brand"} else "page metadata",
            identity_kind=identity_kind,
        )

    if not expected_identity:
        return None
    signals = _page_identity_signals(source, extracted_text)
    conflicting = _conflicting_unlabeled_identity(signals, expected_identity)
    if conflicting:
        return _runtime_identity_evidence(
            source,
            conflicting,
            "conflicting page-local identity signal",
        )
    supporting_channels = [
        channel
        for channel, values in signals.items()
        if any(_identity_matches(expected_identity, value) for value in values)
    ]
    if len(supporting_channels) < 2:
        return None
    return RuntimeAuthorityEvidence(
        controlled_identity=expected_identity,
        identity_kind="manufacturer",
        domain=source.manufacturer_domain,
        reason=(
            "The retrieved exact-MPN product page contains coherent page-local "
            f"identity signals in: {', '.join(supporting_channels)}."
        ),
    )


def _runtime_identity_evidence(
    source: ManufacturerSource,
    identity: str,
    signal: str,
    *,
    identity_kind: RuntimeIdentityKind = "manufacturer",
) -> RuntimeAuthorityEvidence:
    return RuntimeAuthorityEvidence(
        controlled_identity=identity,
        identity_kind=identity_kind,
        domain=source.manufacturer_domain,
        reason=f"The retrieved exact-MPN page contains an {signal}.",
        evidence_reference=source.url,
    )


class _PageIdentityParser(HTMLParser):
    """Collect identity-bearing page channels without treating attributes as proof."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.open_graph: list[str] = []
        self.site_name: list[str] = []
        self.description: list[str] = []
        self.structured: list[str] = []
        self.structured_identities: list[tuple[str, RuntimeIdentityKind]] = []
        self.visible: list[str] = []
        self._hidden_depth = 0
        self._title_depth = 0
        self._script_depth = 0
        self._script_buffer: list[str] = []
        self._script_chars = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript", "template"}:
            self._hidden_depth += 1
        if tag == "script":
            self._script_depth += 1
            self._script_buffer = []
            self._script_chars = 0
        if tag == "title":
            self._title_depth += 1
        if tag != "meta":
            return
        key = (attributes.get("property") or attributes.get("name") or attributes.get("itemprop", "")).casefold()
        value = attributes.get("content", "").strip()
        if not value:
            return
        if key == "og:title":
            self.open_graph.append(value)
        elif key == "og:site_name":
            self.open_graph.append(value)
            self.site_name.append(value)
        elif key == "og:description":
            self.open_graph.append(value)
        elif key in {"description", "product:description"}:
            self.description.append(value)
        elif key in {"brand", "manufacturer", "manufacturername"}:
            self.structured.append(value)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "script" and self._script_depth:
            self._extract_script_identities("".join(self._script_buffer))
            self._script_depth -= 1
        if tag in {"script", "style", "noscript", "template"} and self._hidden_depth:
            self._hidden_depth -= 1
        if tag == "title" and self._title_depth:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._script_depth:
            remaining = 50_000 - self._script_chars
            if remaining > 0:
                fragment = data[:remaining]
                self._script_buffer.append(fragment)
                self._script_chars += len(fragment)
            return
        value = " ".join(data.split())
        if not value or self._hidden_depth:
            return
        if self._title_depth:
            self.title.append(value)
        else:
            self.visible.append(value)

    def _extract_script_identities(self, script: str) -> None:
        """Read only explicitly keyed identity fields from page-local state."""
        patterns = (
            ("brand", r"[\"']?brand(?:_name)?[\"']?\s*:\s*[\"']([^\"']{2,80})[\"']"),
            ("manufacturer", r"[\"']?manufacturer(?:_name)?[\"']?\s*:\s*[\"']([^\"']{2,80})[\"']"),
        )
        for kind, pattern in patterns:
            for match in re.finditer(pattern, script, flags=re.IGNORECASE):
                value = " ".join(match.group(1).split()).strip()
                if value and (value, kind) not in self.structured_identities:
                    self.structured_identities.append((value, kind))


def _page_identity_signals(
    source: ManufacturerSource,
    extracted_text: str,
) -> dict[str, list[str]]:
    parser = _PageIdentityParser()
    if source.source_type == "web":
        payload = source.content if isinstance(source.content, bytes) else source.content.encode("utf-8")
        try:
            parser.feed(payload.decode("utf-8", errors="replace"))
            parser.close()
        except Exception:
            # Page-local metadata is supplemental; malformed HTML cannot make
            # the verified source unusable by itself.
            pass
    title_values = [source.source_name, *parser.title]
    return {
        "title": title_values,
        "open_graph": parser.open_graph,
        "site_name": parser.site_name,
        # ``parser.visible`` excludes the title and hidden scripts, keeping
        # this channel independent from title/OpenGraph metadata.
        "visible_text": parser.visible,
        "description": parser.description,
        "structured_metadata": parser.structured,
        "structured_brand": [value for value, kind in parser.structured_identities if kind == "brand"],
        "structured_manufacturer": [
            value for value, kind in parser.structured_identities if kind == "manufacturer"
        ],
    }


def _extract_structured_page_identities(
    source: ManufacturerSource,
) -> list[tuple[str, RuntimeIdentityKind]]:
    """Extract typed identities from bounded page-local application state."""
    if source.source_type != "web":
        return []
    parser = _PageIdentityParser()
    payload = source.content if isinstance(source.content, bytes) else source.content.encode("utf-8")
    try:
        parser.feed(payload.decode("utf-8", errors="replace"))
        parser.close()
    except Exception:
        return []
    return list(parser.structured_identities)


def _conflicting_unlabeled_identity(
    signals: dict[str, list[str]],
    expected_identity: str,
) -> str | None:
    """Find a distinct identity only in direct identity-bearing metadata.

    Visible descriptions and sentences remain useful for confirming the
    expected catalogue identity, but they are not allowed to manufacture a
    competing identity from ordinary words such as ``This`` or ``Smart``.
    """
    # ``og:site_name`` identifies the publisher/seller context, not the
    # product manufacturer. It remains available to the positive signal
    # combiner, but cannot manufacture a competing identity assertion.
    candidates = [*signals.get("structured_metadata", ())]
    for candidate in candidates:
        if not _identity_matches(expected_identity, candidate):
            return candidate
    return None


def _identity_matches(expected: str, observed: str) -> bool:
    expected_compact = _compact_identity(expected)
    observed_compact = _compact_identity(observed)
    if not expected_compact or not observed_compact:
        return False
    # Compact equality handles punctuation/whitespace variants such as
    # ``ACME Tools`` and ``ACME-Tools``.  Do not accept substring matches:
    # ``Festo`` and ``Festool`` are distinct identities.
    if expected_compact == observed_compact:
        return True
    expected_tokens = set(_identity_tokens(expected))
    observed_tokens = set(_identity_tokens(observed))
    return bool(expected_tokens.intersection(observed_tokens))


def _extract_labeled_page_identities(
    extracted_text: str,
) -> list[tuple[str, RuntimeIdentityKind]]:
    values: list[tuple[str, RuntimeIdentityKind]] = []
    patterns = (
        ("manufacturer", r"\bmanufacturer(?:\s+name)?\s*[:\-]\s*([^\n|;]{2,80})"),
        ("brand", r"\bbrand(?:\s+name)?\s*[:\-]\s*([^\n|;]{2,80})"),
        ("manufacturer", r"\bmade\s+by\s*[:\-]\s*([^\n|;]{2,80})"),
    )
    for kind, pattern in patterns:
        for match in re.finditer(pattern, extracted_text, flags=re.IGNORECASE):
            value = re.sub(r"\s+", " ", match.group(1)).strip(" .,-")
            item = (value, kind)
            if value and item not in values:
                values.append(item)
    return values


def _extract_page_identity(extracted_text: str) -> str | None:
    """Extract only explicitly labeled manufacturer/brand text from a page."""
    patterns = (
        r"\bmanufacturer(?:\s+name)?\s*[:\-]\s*([^\n|;]{2,80})",
        r"\bbrand(?:\s+name)?\s*[:\-]\s*([^\n|;]{2,80})",
        r"\bmade\s+by\s*[:\-]\s*([^\n|;]{2,80})",
    )
    for pattern in patterns:
        match = re.search(pattern, extracted_text, flags=re.IGNORECASE)
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip(" .,-")
            if value:
                return value
    return None


def _catalogue_identity_hint_from_text(extracted_text: str) -> str | None:
    """Return no hostname-derived identity; kept as an explicit seam.

    The default runtime verifier does not have the catalogue row here, so it
    cannot safely infer a manufacturer from arbitrary page prose. Callers
    that need catalogue-aware identity matching should provide the existing
    ``site_identity_verifier`` hook, which receives the row and page text.
    """
    return None


def _identity_tokens(value: str) -> set[str]:
    """Return conservative word tokens for identity comparison."""
    value = value.casefold()
    return {
        token.casefold()
        for token in re.findall(r"[a-z0-9]+", value)
        if len(token) > 1
    }


def _compact_identity(value: str) -> str:
    """Remove presentation separators for deterministic identity comparison."""
    return "".join(character for character in value.casefold() if character.isalnum())
