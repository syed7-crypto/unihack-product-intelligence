"""Ephemeral, fail-closed runtime identity and source-policy resolution."""

from __future__ import annotations

from collections.abc import Callable
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
    max_results: int = 10,
) -> IdentityResolutionResult:
    """Resolve known identities or safely attempt one ephemeral runtime policy.

    A search result is never authority evidence. Runtime resolution requires an
    injected deterministic authority verifier before retrieval is attempted.
    Successful runtime policies are returned only in this result and are never
    written to the controlled registry.
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

    if search_provider is None or enrichment_provider is None or authority_verifier is None:
        return IdentityResolutionResult(
            state="unknown",
            reason="No controlled identity or runtime authority verifier was available.",
        )
    if max_results < 1:
        raise ValueError("max_results must be positive.")

    diagnostics: list[str] = []
    try:
        search_results = search_provider.search(row.Mfg_Part_Num.strip(), max_results)
    except Exception as error:
        return IdentityResolutionResult(
            state="unknown",
            reason="Runtime identity search failed; no policy was created.",
            diagnostics=[str(error) or "Search failed."],
        )

    for candidate in search_results:
        parsed = urlparse(candidate.url)
        domain = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme.casefold() != "https" or not domain:
            diagnostics.append("Candidate rejected because it is not an HTTPS URL.")
            continue
        try:
            evidence = authority_verifier(row, candidate)
        except Exception as error:
            diagnostics.append(f"Authority verification failed: {error}")
            continue
        if evidence is None:
            diagnostics.append("Candidate had no independent authority evidence.")
            continue
        evidence_domain = normalize_reference_value(evidence.domain).rstrip(".")
        if evidence_domain != domain:
            diagnostics.append("Authority evidence domain did not match the candidate domain.")
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
            state="resolvable",
            resolved_identity=evidence.controlled_identity,
            identity_kind=evidence.identity_kind,
            approved_domains=(domain,),
            reason="Authority evidence and exact-MPN source verification succeeded.",
            diagnostics=diagnostics,
            runtime_policy=runtime_policy,
            verified_sources=[retrieval.source],
        )

    return IdentityResolutionResult(
        state="unknown",
        reason="No candidate satisfied authority and exact-MPN verification.",
        diagnostics=diagnostics,
    )
