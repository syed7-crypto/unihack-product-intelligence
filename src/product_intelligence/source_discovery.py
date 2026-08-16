"""Governed discovery of candidate manufacturer source URLs.

Discovery is intentionally untrusted. This module never creates a
``NormalizedSource`` or evidence object and never treats a search result as
authoritative. Verification remains the responsibility of
``ManufacturerEnrichmentProvider``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field, model_validator

from .catalog_input import CatalogInputRow
from .manufacturer_enrichment import ManufacturerEnrichmentProvider, ManufacturerSource
from .reference_data import ReferenceResolutionResult, normalize_reference_value


SourceKind = Literal["webpage", "pdf", "unknown"]
CandidateStatus = Literal["candidate", "rejected"]
DiscoveryStatus = Literal["found", "no_candidates", "failed"]
VerificationStatus = Literal["verified", "failed", "rejected"]


class SearchProviderError(RuntimeError):
    """Explicit failure from a configured real search provider."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class SearchTransportResponse:
    """Small transport response used by the real provider and tests."""

    status_code: int
    body: bytes


class SearchTransport(Protocol):
    def __call__(self, request: Request, timeout: float) -> SearchTransportResponse:
        ...


class SearchResult(BaseModel):
    """Untrusted result returned by a search provider."""

    url: str = Field(min_length=1)
    title: str = ""
    snippet: str = ""


class SourceSearchProvider(Protocol):
    def search(self, query: str, max_results: int) -> list[SearchResult]:
        """Return untrusted search results in provider order."""


class BraveSearchProvider:
    """Real Brave Web Search API adapter.

    This adapter only translates API results into ``SearchResult`` objects.
    It does not apply manufacturer policy or verify sources.
    """

    DEFAULT_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = 15.0,
        country: str | None = None,
        search_lang: str | None = None,
        transport: SearchTransport | None = None,
    ) -> None:
        self.api_key = api_key.strip() if api_key and api_key.strip() else None
        self.endpoint = endpoint
        self.timeout = timeout
        self.country = country
        self.search_lang = search_lang
        self._transport = transport or _default_search_transport

    @classmethod
    def from_environment(cls) -> "BraveSearchProvider":
        """Create a provider from BRAVE_SEARCH_* environment configuration.

        A missing key is intentionally retained as a provider state and is
        reported by ``search``; construction itself never fabricates results.
        """
        try:
            from dotenv import load_dotenv

            from pathlib import Path

            load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        except ImportError:
            pass
        timeout_text = os.getenv("BRAVE_SEARCH_TIMEOUT", "15")
        try:
            timeout = float(timeout_text)
        except ValueError:
            raise SearchProviderError(
                "invalid_configuration", "BRAVE_SEARCH_TIMEOUT must be numeric."
            ) from None
        return cls(
            os.getenv("BRAVE_SEARCH_API_KEY"),
            endpoint=os.getenv("BRAVE_SEARCH_ENDPOINT", cls.DEFAULT_ENDPOINT),
            timeout=timeout,
            country=os.getenv("BRAVE_SEARCH_COUNTRY"),
            search_lang=os.getenv("BRAVE_SEARCH_LANG"),
        )

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.api_key:
            raise SearchProviderError(
                "missing_api_key",
                "BRAVE_SEARCH_API_KEY is not configured; real discovery is unavailable.",
            )
        if not query.strip():
            raise SearchProviderError("invalid_query", "Search query must not be empty.")
        if max_results < 1:
            raise SearchProviderError("invalid_limit", "max_results must be positive.")

        params = {"q": query.strip(), "count": str(min(max_results, 20))}
        if self.country:
            params["country"] = self.country
        if self.search_lang:
            params["search_lang"] = self.search_lang
        request = Request(
            f"{self.endpoint}?{urlencode(params)}",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
                "User-Agent": "UniHackProductIntelligence/1.0",
            },
            method="GET",
        )
        try:
            response = self._transport(request, self.timeout)
        except HTTPError as error:
            code = "rate_limited" if error.code == 429 else "http_error"
            raise SearchProviderError(code, f"Search API returned HTTP {error.code}.") from error
        except (URLError, TimeoutError, OSError) as error:
            raise SearchProviderError("provider_unavailable", str(error) or "Search request failed.") from error

        if response.status_code == 429:
            raise SearchProviderError("rate_limited", "Search API rate limit was reached.")
        if response.status_code < 200 or response.status_code >= 300:
            raise SearchProviderError(
                "http_error", f"Search API returned HTTP {response.status_code}."
            )
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SearchProviderError("malformed_response", "Search API returned invalid JSON.") from error
        results = payload.get("web", {}).get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise SearchProviderError(
                "malformed_response", "Search API response did not contain web.results."
            )

        normalized: list[SearchResult] = []
        for item in results:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                raise SearchProviderError(
                    "malformed_response", "A search result did not contain a URL."
                )
            try:
                url = _normalize_search_url(item["url"])
            except ValueError as error:
                raise SearchProviderError("invalid_result_url", str(error)) from error
            title = item.get("title", "")
            description = item.get("description", "")
            if not isinstance(title, str) or not isinstance(description, str):
                raise SearchProviderError(
                    "malformed_response", "A search result title or description was not text."
                )
            normalized.append(SearchResult(url=url, title=title, snippet=description))
        return normalized


def _default_search_transport(request: Request, timeout: float) -> SearchTransportResponse:
    with urlopen(request, timeout=timeout) as response:  # nosec B310: configured API endpoint
        return SearchTransportResponse(response.status, response.read())


def _normalize_search_url(value: str) -> str:
    raw = value.strip()
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Search result contained an invalid HTTP(S) URL.")
    hostname = parsed.hostname.casefold()
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or "/", parsed.query, ""))


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


class DiscoveryPilotRowResult(BaseModel):
    """Structured diagnostic for one explicitly selected pilot row."""

    mfg_part_num: str
    manufacturer_candidate: str
    brand_candidates: dict[str, str | None]
    discovery: SourceDiscoveryResult | None = None
    verification: DiscoveredSourceVerificationResult | None = None
    error: str | None = None


def run_discovery_pilot(
    rows: Sequence[CatalogInputRow],
    policy_for_row: Callable[[CatalogInputRow], ManufacturerSourcePolicy | None],
    search_provider: SourceSearchProvider,
    enrichment_provider: ManufacturerEnrichmentProvider,
) -> list[DiscoveryPilotRowResult]:
    """Run discovery for caller-selected rows only.

    This helper never reads the expected-output CSV and never expands the
    caller's row selection. A missing policy is an explicit pilot failure.
    """
    reports: list[DiscoveryPilotRowResult] = []
    for row in rows:
        policy = policy_for_row(row)
        if policy is None:
            reports.append(
                DiscoveryPilotRowResult(
                    mfg_part_num=row.Mfg_Part_Num,
                    manufacturer_candidate=row.Part_Manuf,
                    brand_candidates=row.brand_candidates(),
                    error="No explicit manufacturer source policy was configured for this row.",
                )
            )
            continue
        try:
            verification = discover_and_verify_sources(
                row, policy, search_provider, enrichment_provider
            )
            discovery = verification.discovery
            reports.append(
                DiscoveryPilotRowResult(
                    mfg_part_num=row.Mfg_Part_Num,
                    manufacturer_candidate=row.Part_Manuf,
                    brand_candidates=row.brand_candidates(),
                    discovery=discovery,
                    verification=verification,
                )
            )
        except (SearchProviderError, RuntimeError, ValueError, OSError) as error:
            reports.append(
                DiscoveryPilotRowResult(
                    mfg_part_num=row.Mfg_Part_Num,
                    manufacturer_candidate=row.Part_Manuf,
                    brand_candidates=row.brand_candidates(),
                    error=str(error),
                )
            )
    return reports


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
