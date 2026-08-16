"""Governed discovery of candidate manufacturer source URLs.

Discovery is intentionally untrusted. This module never creates a
``NormalizedSource`` or evidence object and never treats a search result as
authoritative. Verification remains the responsibility of
``ManufacturerEnrichmentProvider``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Literal, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

from .catalog_input import CatalogInputRow
from .manufacturer_enrichment import ManufacturerEnrichmentProvider, ManufacturerSource
from .reference_data import ReferenceResolutionResult, normalize_reference_value


SourceKind = Literal["webpage", "pdf", "unknown"]
CandidateStatus = Literal["candidate", "rejected"]
DiscoveryStatus = Literal["found", "no_candidates", "failed"]
VerificationStatus = Literal["verified", "failed", "rejected"]


class SearchResult(BaseModel):
    """Untrusted result returned by a search provider."""

    url: str = Field(min_length=1)
    title: str = ""
    snippet: str = ""


class SourceSearchProvider(Protocol):
    def search(self, query: str, max_results: int) -> list[SearchResult]:
        """Return untrusted search results in provider order."""


class InMemorySourceSearchProvider:
    """Deterministic search provider for tests and local demonstrations."""

    def __init__(self, results_by_query: Mapping[str, Sequence[SearchResult]]) -> None:
        self._results = {query: list(results) for query, results in results_by_query.items()}
        self.queries: list[tuple[str, int]] = []

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        self.queries.append((query, max_results))
        return list(self._results.get(query, ()))[:max_results]


class ManufacturerSourcePolicy(BaseModel):
    """Explicit policy for which discovered candidates may be verified."""

    manufacturer_name: str | None = None
    approved_domains: tuple[str, ...] = ()
    allowed_source_kinds: tuple[SourceKind, ...] = ("webpage", "pdf")
    query_templates: tuple[str, ...] = (
        "{part_number}",
        "{part_number} {manufacturer}",
        "{part_number} {brand}",
    )

    @model_validator(mode="after")
    def validate_policy(self) -> "ManufacturerSourcePolicy":
        normalized = tuple(
            normalize_reference_value(domain).rstrip(".")
            for domain in self.approved_domains
            if domain.strip()
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError("Approved manufacturer domains must be unique.")
        if not self.query_templates:
            raise ValueError("At least one discovery query template is required.")
        self.approved_domains = normalized
        return self

    def domain_allowed(self, domain: str) -> bool:
        normalized = domain.casefold().rstrip(".")
        return any(
            normalized == approved or normalized.endswith("." + approved)
            for approved in self.approved_domains
        )


class DiscoveredSourceCandidate(BaseModel):
    """A search result plus deterministic policy metadata, never evidence."""

    url: str = Field(min_length=1)
    domain: str = ""
    title: str = ""
    snippet: str = ""
    source_kind: SourceKind
    discovery_query: str
    discovery_rank: int = Field(ge=1)
    manufacturer_hint: str | None = None
    exact_mpn_in_result: bool | None = None
    discovery_reason: str
    status: CandidateStatus


class SourceDiscoveryResult(BaseModel):
    """Deterministic candidate-only discovery output."""

    part_number: str
    candidates: list[DiscoveredSourceCandidate] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    status: DiscoveryStatus


class SourceVerificationDiagnostic(BaseModel):
    """Verification outcome retaining discovery context and failure reason."""

    url: str
    discovery_query: str
    discovery_rank: int = Field(ge=1)
    title: str = ""
    snippet: str = ""
    policy_decision: CandidateStatus
    verification_status: VerificationStatus
    error: str | None = None


class DiscoveredSourceVerificationResult(BaseModel):
    """Discovery plus only successfully verified manufacturer sources."""

    part_number: str
    discovery: SourceDiscoveryResult
    verified_sources: list[ManufacturerSource] = Field(default_factory=list)
    diagnostics: list[SourceVerificationDiagnostic] = Field(default_factory=list)


def generate_discovery_queries(
    catalogue_row: CatalogInputRow,
    policy: ManufacturerSourcePolicy,
    *,
    manufacturer_reference: ReferenceResolutionResult | None = None,
    brand_reference: ReferenceResolutionResult | None = None,
) -> list[str]:
    """Generate a small deterministic query list from controlled identity data."""
    manufacturer = _resolved_value(manufacturer_reference) or policy.manufacturer_name
    brand = _resolved_value(brand_reference)
    values = {
        "part_number": catalogue_row.Mfg_Part_Num.strip(),
        "manufacturer": manufacturer or "",
        "brand": brand or "",
    }
    queries: list[str] = []
    for template in policy.query_templates:
        try:
            query = " ".join(template.format(**values).split())
        except (KeyError, ValueError):
            continue
        if query and query not in queries:
            queries.append(query)
    return queries


def discover_manufacturer_sources(
    catalogue_row: CatalogInputRow,
    policy: ManufacturerSourcePolicy,
    search_provider: SourceSearchProvider,
    *,
    manufacturer_reference: ReferenceResolutionResult | None = None,
    brand_reference: ReferenceResolutionResult | None = None,
    max_results_per_query: int = 10,
) -> SourceDiscoveryResult:
    """Search for candidates and apply policy metadata without verification."""
    if max_results_per_query < 1:
        raise ValueError("max_results_per_query must be positive.")

    queries = generate_discovery_queries(
        catalogue_row,
        policy,
        manufacturer_reference=manufacturer_reference,
        brand_reference=brand_reference,
    )
    candidates: list[DiscoveredSourceCandidate] = []
    errors: list[str] = []
    seen_urls: set[str] = set()
    manufacturer_hint = _resolved_value(manufacturer_reference) or policy.manufacturer_name

    for query in queries:
        try:
            results = search_provider.search(query, max_results_per_query)
        except Exception as error:
            errors.append(f"Search failed for query '{query}': {error}")
            continue
        for rank, result in enumerate(results, start=1):
            if result.url in seen_urls:
                continue
            seen_urls.add(result.url)
            candidate = _candidate_from_result(
                result,
                query,
                rank,
                catalogue_row.Mfg_Part_Num,
                policy,
                manufacturer_hint,
            )
            candidates.append(candidate)

    status: DiscoveryStatus
    if candidates:
        status = "found"
    elif errors:
        status = "failed"
    else:
        status = "no_candidates"
    return SourceDiscoveryResult(
        part_number=catalogue_row.Mfg_Part_Num,
        candidates=candidates,
        queries=queries,
        errors=errors,
        status=status,
    )


def discover_and_verify_sources(
    catalogue_row: CatalogInputRow,
    policy: ManufacturerSourcePolicy,
    search_provider: SourceSearchProvider,
    enrichment_provider: ManufacturerEnrichmentProvider,
    *,
    manufacturer_reference: ReferenceResolutionResult | None = None,
    brand_reference: ReferenceResolutionResult | None = None,
    max_results_per_query: int = 10,
) -> DiscoveredSourceVerificationResult:
    """Discover candidates, then verify only policy-approved URLs.

    The enrichment provider remains the sole authority for retrieval and exact
    MPN verification. This function never creates normalized sources itself.
    """
    discovery = discover_manufacturer_sources(
        catalogue_row,
        policy,
        search_provider,
        manufacturer_reference=manufacturer_reference,
        brand_reference=brand_reference,
        max_results_per_query=max_results_per_query,
    )
    verified_sources: list[ManufacturerSource] = []
    diagnostics: list[SourceVerificationDiagnostic] = []
    manufacturer_unresolved = (
        manufacturer_reference is not None
        and manufacturer_reference.status != "resolved"
    )
    for candidate in discovery.candidates:
        if candidate.status != "candidate":
            diagnostics.append(
                _diagnostic(candidate, "rejected", "Candidate rejected by discovery policy.")
            )
            continue
        if manufacturer_unresolved:
            diagnostics.append(
                _diagnostic(
                    candidate,
                    "rejected",
                    "Manufacturer reference is unresolved; candidate was not passed to verification.",
                )
            )
            continue
        retrieval = enrichment_provider.retrieve_source(candidate.url, catalogue_row.Mfg_Part_Num)
        if retrieval.success and retrieval.source is not None:
            verified_sources.append(retrieval.source)
            diagnostics.append(_diagnostic(candidate, "verified", None))
        else:
            diagnostics.append(
                _diagnostic(candidate, "failed", retrieval.error or "Source verification failed.")
            )
    return DiscoveredSourceVerificationResult(
        part_number=catalogue_row.Mfg_Part_Num,
        discovery=discovery,
        verified_sources=verified_sources,
        diagnostics=diagnostics,
    )


def _candidate_from_result(
    result: SearchResult,
    query: str,
    rank: int,
    part_number: str,
    policy: ManufacturerSourcePolicy,
    manufacturer_hint: str | None,
) -> DiscoveredSourceCandidate:
    parsed = urlparse(result.url)
    domain = (parsed.hostname or "").casefold().rstrip(".")
    kind = _source_kind(result.url)
    exact = _contains_exact_identifier(
        f"{result.title}\n{result.snippet}\n{result.url}", part_number
    )
    if parsed.scheme != "https":
        status: CandidateStatus = "rejected"
        reason = "Candidate is not an HTTPS URL."
    elif kind not in policy.allowed_source_kinds:
        status = "rejected"
        reason = "Candidate source type is not allowed by policy."
    elif not policy.domain_allowed(domain):
        status = "rejected"
        reason = "Candidate domain is not in the explicit manufacturer-domain policy."
    else:
        status = "candidate"
        reason = "Candidate matches the explicit discovery policy; retrieval verification is still required."
    return DiscoveredSourceCandidate(
        url=result.url,
        domain=domain,
        title=result.title,
        snippet=result.snippet,
        source_kind=kind,
        discovery_query=query,
        discovery_rank=rank,
        manufacturer_hint=manufacturer_hint,
        exact_mpn_in_result=exact,
        discovery_reason=reason,
        status=status,
    )


def _source_kind(url: str) -> SourceKind:
    path = PurePosixPath(urlparse(url).path.casefold())
    if path.suffix == ".pdf":
        return "pdf"
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"}:
        return "webpage"
    return "unknown"


def _contains_exact_identifier(text: str, identifier: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9]){re.escape(identifier.strip())}(?![A-Za-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _resolved_value(reference: ReferenceResolutionResult | None) -> str | None:
    if reference is None or reference.status != "resolved":
        return None
    if isinstance(reference.resolved_value, str):
        return reference.resolved_value
    return None


def _diagnostic(
    candidate: DiscoveredSourceCandidate,
    verification_status: VerificationStatus,
    error: str | None,
) -> SourceVerificationDiagnostic:
    return SourceVerificationDiagnostic(
        url=candidate.url,
        discovery_query=candidate.discovery_query,
        discovery_rank=candidate.discovery_rank,
        title=candidate.title,
        snippet=candidate.snippet,
        policy_decision=candidate.status,
        verification_status=verification_status,
        error=error,
    )
