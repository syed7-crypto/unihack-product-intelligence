"""Deterministic, pre-retrieval candidate ranking.

Ranking is an efficiency and explanation aid only.  It never creates source
evidence and never replaces approved-domain, retrieval, exact-MPN, or page
identity verification.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field

from .mpn_normalization import normalize_mpn
from .reference_data import normalize_reference_value


CandidateDecision = Literal["strong", "plausible", "bad"]


class CandidateRanking(BaseModel):
    """Explainable ranking result for one untrusted search candidate."""

    decision: CandidateDecision
    score: int
    reasons: list[str] = Field(default_factory=list)
    page_type: Literal["product", "collection_or_search", "other"]
    visible_mpn_match: bool
    identity_match: bool


def rank_candidate(
    *,
    url: str,
    title: str,
    snippet: str,
    expected_mpn: str,
    expected_identities: tuple[str, ...] = (),
    expected_description: str = "",
    approved_domain: bool = False,
    source_role: Literal["manufacturer", "secondary"] = "manufacturer",
    policy_status: Literal["candidate", "rejected"] = "candidate",
    exact_mpn_in_result: bool | None = None,
) -> CandidateRanking:
    """Rank using only search-result metadata and explicit policy metadata.

    Search metadata is never treated as proof.  In particular, an MPN found
    only in a URL cannot produce a strong decision.
    """
    visible = f"{title}\n{snippet}"
    visible_mpn = _contains_mpn(visible, expected_mpn)
    identity_match, identity_conflict = _identity_signal(
        visible, expected_identities, expected_description
    )
    page_type = _page_type(url)
    score = 0
    reasons: list[str] = []

    if policy_status == "rejected":
        return CandidateRanking(
            decision="bad", score=-100,
            reasons=["Candidate is outside the approved source policy."],
            page_type=page_type, visible_mpn_match=visible_mpn,
            identity_match=identity_match,
        )

    if approved_domain:
        score += 3
        reasons.append("Candidate domain is policy-approved.")
    if source_role == "secondary":
        score += 1
        reasons.append("Candidate is an approved secondary-source type.")
    if visible_mpn:
        score += 3
        reasons.append("The MPN appears in visible search metadata.")
    elif exact_mpn_in_result is False:
        reasons.append("Search metadata does not show the MPN; retrieval verification remains required.")
    else:
        reasons.append("The MPN was not established by search metadata.")

    if identity_match:
        score += 3
        reasons.append("Manufacturer/brand or product-description signals match.")
    if page_type == "product":
        score += 2
        reasons.append("URL resembles a product page.")
    elif page_type == "collection_or_search":
        score -= 3
        reasons.append("URL resembles a collection, category, or search page.")

    if identity_conflict:
        return CandidateRanking(
            decision="bad", score=score - 8,
            reasons=reasons + ["Visible candidate identity conflicts with catalogue identity."],
            page_type=page_type, visible_mpn_match=visible_mpn,
            identity_match=False,
        )

    # A collection/search URL with only a URL-level MPN is not worth a fetch.
    # A missing MPN in title/snippet alone is not BAD because search metadata
    # is incomplete and the authoritative page may still contain it.
    if page_type == "collection_or_search" and not visible_mpn and exact_mpn_in_result:
        return CandidateRanking(
            decision="bad", score=score - 5,
            reasons=reasons + ["MPN appears only in a collection/search URL."],
            page_type=page_type, visible_mpn_match=False,
            identity_match=identity_match,
        )

    policy_identity_support = approved_domain and source_role == "manufacturer"
    strong = (
        approved_domain
        and visible_mpn
        and (identity_match or policy_identity_support)
        and page_type == "product"
    )
    decision: CandidateDecision = "strong" if strong else "plausible"
    if strong:
        if policy_identity_support and not identity_match:
            reasons.append("Controlled manufacturer policy supplies the identity signal.")
        reasons.append("Strong pre-retrieval recommendation; authoritative verification is still mandatory.")
    else:
        reasons.append("Plausible candidate; authoritative verification is still mandatory.")
    return CandidateRanking(
        decision=decision, score=score, reasons=reasons,
        page_type=page_type, visible_mpn_match=visible_mpn,
        identity_match=identity_match,
    )


def _contains_mpn(text: str, expected_mpn: str) -> bool:
    normalized_text = normalize_mpn(text)
    normalized_mpn = normalize_mpn(expected_mpn)
    return normalized_mpn in normalized_text


def _identity_signal(
    text: str,
    identities: tuple[str, ...],
    description: str,
) -> tuple[bool, bool]:
    text_normalized = normalize_reference_value(text)
    text_compact = _compact(text_normalized)
    text_tokens = _tokens(text_normalized)
    expected_tokens = _tokens(" ".join((*identities, description)))
    identity_match = False
    for identity in identities:
        normalized = normalize_reference_value(identity)
        compact = _compact(normalized)
        if compact and compact in text_compact:
            identity_match = True
            break
        if _tokens(normalized).intersection(text_tokens):
            identity_match = True
            break
    if not identity_match and description:
        description_tokens = _tokens(description)
        identity_match = len(description_tokens.intersection(text_tokens)) >= (
            1 if len(description_tokens) <= 2 else 2
        )
    if not identities and not description:
        return False, False
    # A single generic category word is not a contradiction.  Require at
    # least two meaningful visible tokens before treating metadata as an
    # explicit competing identity.
    conflict = len(_tokens(text_normalized)) >= 2 and not identity_match
    return identity_match, conflict


def _page_type(url: str) -> Literal["product", "collection_or_search", "other"]:
    parsed = urlparse(url)
    path = parsed.path.casefold()
    query = parse_qs(parsed.query)
    if any(part in path.split("/") for part in ("collection", "collections", "category", "categories", "search", "results")):
        return "collection_or_search"
    if "page" in query or "search" in query:
        return "collection_or_search"
    if any(part in path.split("/") for part in ("product", "products", "item", "p")):
        return "product"
    return "other"


def _tokens(value: str) -> set[str]:
    stop_words = {
        "and", "the", "with", "for", "from", "model", "product", "item",
        "part", "number", "new", "page", "men", "mens", "women", "womens",
        "collection", "collections", "category", "categories", "search", "results",
        "accessories", "similar", "previously", "verified", "source",
    }
    return {
        token for token in re.findall(r"[a-z0-9]+", value.casefold())
        if token not in stop_words
        and len(token) >= 3
        and any(character.isalpha() for character in token)
        and not any(character.isdigit() for character in token)
    }


def _compact(value: str) -> str:
    return "".join(character for character in value if character.isalnum())
