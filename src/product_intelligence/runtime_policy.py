"""Ephemeral, fail-closed runtime identity and source-policy resolution."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .catalog_input import CatalogInputRow
from .manufacturer_enrichment import ManufacturerEnrichmentProvider, ManufacturerSource
from .pilot_policies import ControlledSourcePolicy, resolve_source_policy_for_row
from .reference_data import BrandReference, ManufacturerReference, normalize_reference_value
from .source_discovery import SearchResult, SourceSearchProvider


IdentityResolutionState = Literal["known", "resolvable", "unknown"]
RuntimeIdentityKind = Literal["manufacturer", "brand"]


class RuntimeAuthorityEvidence(BaseModel):
    """Explicit caller-supplied authority attestation for a candidate domain."""

    controlled_identity: str = Field(min_length=1)
    identity_kind: RuntimeIdentityKind
    domain: str = Field(min_length=1)
    reason: str = Field(min_length=1)


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
    discovery_rank: int = Field(default=1, ge=1)


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


class IdentityResolutionResult(BaseModel):
    """Outcome of controlled identity/source resolution for one row."""

    state: IdentityResolutionState
    resolved_identity: str | None = None
    identity_kind: RuntimeIdentityKind | None = None
    approved_domains: tuple[str, ...] = ()
    reason: str
    diagnostics: list[str] = Field(default_factory=list)
    runtime_policy: ControlledSourcePolicy | None = None
    verified_sources: list[ManufacturerSource] = Field(default_factory=list)


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
            identity_kind="manufacturer",
            approved_domains=known.approved_domains,
            reason="A controlled manufacturer/brand policy was resolved.",
        )

    if search_provider is None or enrichment_provider is None:
        return IdentityResolutionResult(
            state="unknown",
            reason="No controlled identity or runtime authority verifier was available.",
        )
    if max_results < 1:
        raise ValueError("max_results must be positive.")

    if max_candidate_domains < 1 or max_domain_searches < 1:
        raise ValueError("Runtime candidate/search limits must be positive.")

    # Preserve the original injected-verifier contract as a strict compatibility
    # path. The product-first path below verifies identity after retrieving the
    # actual candidate-domain page.
    if authority_verifier is not None:
        return _resolve_with_legacy_authority_verifier(
            row,
            search_provider=search_provider,
            enrichment_provider=enrichment_provider,
            authority_verifier=authority_verifier,
            max_results=max_results,
        )

    diagnostics: list[str] = []
    try:
        if candidate_domain_provider is not None:
            domain_candidates = list(candidate_domain_provider(row))[:max_candidate_domains]
        else:
            search_results = search_provider.search(row.Mfg_Part_Num.strip(), max_results)
            domain_candidates = _domain_candidates_from_search(search_results, max_candidate_domains)
    except Exception as error:
        return IdentityResolutionResult(
            state="unknown",
            reason="Runtime candidate-domain discovery failed; no policy was created.",
            diagnostics=[str(error) or "Search failed."],
        )

    for domain_candidate in domain_candidates:
        domain = _normalize_candidate_domain(domain_candidate.domain)
        if domain is None:
            diagnostics.append("Candidate rejected because it is not an HTTPS URL.")
            continue
        if _is_retailer_domain(domain):
            diagnostics.append(f"Candidate retailer domain rejected: {domain}.")
            continue
        try:
            domain_results = search_provider.search(
                f'site:{domain} "{row.Mfg_Part_Num.strip()}"', max_domain_searches
            )
        except Exception as error:
            diagnostics.append(f"Domain-constrained search failed for {domain}: {error}")
            continue

        candidate_urls = _candidate_urls_for_domain(domain_results, domain)
        if domain_candidate.discovery_url:
            candidate_urls.insert(0, domain_candidate.discovery_url)
        for url in dict.fromkeys(candidate_urls):
            scoped_provider = enrichment_provider.with_approved_domains({domain})
            retrieval = scoped_provider.retrieve_source(url, row.Mfg_Part_Num)
            if not retrieval.success or retrieval.source is None:
                diagnostics.append(retrieval.error or "Exact-MPN source verification failed.")
                continue
            source = retrieval.source
            try:
                normalized = scoped_provider.to_normalized_source(source)
                evidence = (
                    site_identity_verifier(row, domain_candidate, source, normalized.extracted_text)
                    if site_identity_verifier is not None
                    else _deterministic_site_identity(domain_candidate, source, normalized.extracted_text)
                )
            except Exception as error:
                diagnostics.append(f"Site identity verification failed: {error}")
                continue
            if evidence is None:
                diagnostics.append("Candidate site did not provide matching manufacturer identity evidence.")
                continue
            evidence_domain = normalize_reference_value(evidence.domain).rstrip(".")
            if evidence_domain != domain:
                diagnostics.append("Site identity evidence domain did not match the candidate domain.")
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
            )

    return IdentityResolutionResult(
        state="unknown",
        reason="No candidate satisfied authority and exact-MPN verification.",
        diagnostics=diagnostics,
    )


def _resolve_with_legacy_authority_verifier(
    row: CatalogInputRow,
    *,
    search_provider: SourceSearchProvider,
    enrichment_provider: ManufacturerEnrichmentProvider,
    authority_verifier: RuntimeAuthorityVerifier,
    max_results: int,
) -> IdentityResolutionResult:
    diagnostics: list[str] = []
    try:
        search_results = search_provider.search(row.Mfg_Part_Num.strip(), max_results)
    except Exception as error:
        return IdentityResolutionResult(
            state="unknown", reason="Runtime identity search failed; no policy was created.",
            diagnostics=[str(error) or "Search failed."],
        )
    for candidate in search_results:
        parsed = urlparse(candidate.url)
        domain = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme.casefold() != "https" or not domain or _is_retailer_domain(domain):
            diagnostics.append("Candidate rejected by HTTPS/retailer safety checks.")
            continue
        evidence = authority_verifier(row, candidate)
        if evidence is None or normalize_reference_value(evidence.domain).rstrip(".") != domain:
            diagnostics.append("Candidate had no matching independent authority evidence.")
            continue
        scoped_provider = enrichment_provider.with_approved_domains({domain})
        retrieval = scoped_provider.retrieve_source(candidate.url, row.Mfg_Part_Num)
        if not retrieval.success or retrieval.source is None:
            diagnostics.append(retrieval.error or "Exact-MPN source verification failed.")
            continue
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
        )
    return IdentityResolutionResult(
        state="unknown", reason="No candidate satisfied authority and exact-MPN verification.",
        diagnostics=diagnostics,
    )


def _domain_candidates_from_search(
    results: Sequence[SearchResult], max_candidate_domains: int
) -> list[RuntimeDomainCandidate]:
    candidates: list[RuntimeDomainCandidate] = []
    seen: set[str] = set()
    for rank, result in enumerate(results, start=1):
        parsed = urlparse(result.url)
        domain = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme.casefold() != "https" or not domain or domain in seen:
            continue
        seen.add(domain)
        hint = domain.removeprefix("www.").split(".", 1)[0].replace("-", " ")
        candidates.append(RuntimeDomainCandidate(
            domain=domain, identity_hint=hint, discovery_url=None, discovery_rank=rank
        ))
        if len(candidates) >= max_candidate_domains:
            break
    return candidates


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
) -> RuntimeAuthorityEvidence | None:
    hint = normalize_reference_value(candidate.identity_hint or "")
    if not hint:
        return None
    text = normalize_reference_value(extracted_text)
    # Require the proposed identity to occur in the retrieved page itself.
    # Compact comparison only removes presentation separators, allowing
    # legitimate forms such as ``Milwaukee Tool``/``milwaukeetool`` while
    # retaining deterministic exact matching. Search metadata is never used.
    if hint not in text and _compact_identity(hint) not in _compact_identity(text):
        return None
    return RuntimeAuthorityEvidence(
        controlled_identity=candidate.identity_hint.strip(),
        identity_kind="manufacturer",
        domain=source.manufacturer_domain,
        reason="The retrieved exact-MPN page contains the proposed identity in its site text.",
    )


def _compact_identity(value: str) -> str:
    """Remove presentation separators for deterministic identity comparison."""
    return "".join(character for character in value if character.isalnum())
