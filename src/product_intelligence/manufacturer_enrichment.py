"""Controlled retrieval and verification of approved manufacturer sources.

This module deliberately does not discover URLs. Callers must provide an
explicitly approved URL, and the exact manufacturer part number must occur in
the retrieved source before a ``NormalizedSource`` is created.
"""

from __future__ import annotations

import hashlib
import html
import ast
import json
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .extraction import ExtractionError, NormalizedSource, SourceLocation, extract_pdf
from .mpn_normalization import normalize_mpn
from .runtime_timing import RuntimeTimingAccumulator


ManufacturerSourceType = Literal["web", "pdf"]


class ManufacturerSource(BaseModel):
    """A retrieved, exact-MPN-verified manufacturer source."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    url: str = Field(min_length=1)
    source_type: ManufacturerSourceType
    manufacturer_domain: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    content: str | bytes
    exact_mpn_verified: bool

    @model_validator(mode="after")
    def require_verification(self) -> "ManufacturerSource":
        if not self.exact_mpn_verified:
            raise ValueError("ManufacturerSource must contain a verified exact MPN.")
        return self


class RetrievalResult(BaseModel):
    """Explicit success/failure result for one controlled source retrieval."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool
    source: ManufacturerSource | None = None
    error: str | None = None
    code: str | None = None
    http_status: int | None = None
    content_type: str | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "RetrievalResult":
        if self.success and self.source is None:
            raise ValueError("Successful retrievals require a source.")
        if not self.success and self.source is not None:
            raise ValueError("Failed retrievals must not contain a source.")
        if not self.success and not self.error:
            raise ValueError("Failed retrievals require an error message.")
        return self


@dataclass(frozen=True)
class RetrievedPayload:
    """Small transport-neutral response used by the default and test fetchers."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str | None = None


class SourceFetcher(Protocol):
    def __call__(self, url: str, timeout: float) -> RetrievedPayload:
        ...


DEFAULT_APPROVED_DOMAINS = frozenset(
    {"www.frigidaire.com", "frigidaire.com", "frigidaire.bynder.com"}
)


class ManufacturerEnrichmentProvider:
    """Retrieve only explicitly supplied URLs from approved domains."""

    def __init__(
        self,
        *,
        approved_domains: set[str] | frozenset[str] | None = None,
        timeout: float = 20.0,
        fetcher: SourceFetcher | None = None,
        runtime_timing: RuntimeTimingAccumulator | None = None,
    ) -> None:
        self.approved_domains = frozenset(
            domain.casefold().rstrip(".")
            for domain in (approved_domains or DEFAULT_APPROVED_DOMAINS)
        )
        self.timeout = timeout
        self._fetcher = fetcher or _fetch_url
        self._runtime_timing = runtime_timing

    def with_approved_domains(
        self,
        approved_domains: set[str] | frozenset[str],
    ) -> "ManufacturerEnrichmentProvider":
        """Return a verifier with an explicit policy-scoped domain allowlist.

        The transport, timeout, and exact-MPN verification implementation are
        reused unchanged. This is used when a selected discovery policy must
        be passed into the retrieval boundary.
        """
        return ManufacturerEnrichmentProvider(
            approved_domains=approved_domains,
            timeout=self.timeout,
            fetcher=self._fetcher,
            runtime_timing=self._runtime_timing,
        )

    def with_runtime_timing(
        self, runtime_timing: RuntimeTimingAccumulator | None
    ) -> "ManufacturerEnrichmentProvider":
        """Return the same provider with optional diagnostic timing attached."""
        return ManufacturerEnrichmentProvider(
            approved_domains=self.approved_domains,
            timeout=self.timeout,
            fetcher=self._fetcher,
            runtime_timing=runtime_timing,
        )

    def retrieve_source(
        self,
        url: str,
        expected_mpn: str,
        *,
        expected_identity: str | None = None,
        expected_description: str | None = None,
    ) -> RetrievalResult:
        if self._runtime_timing is None:
            return self._retrieve_source(
                url,
                expected_mpn,
                expected_identity=expected_identity,
                expected_description=expected_description,
            )
        with self._runtime_timing.measure(
            "source_retrieval_duration_seconds", "source_retrieval_calls"
        ):
            return self._retrieve_source(
                url,
                expected_mpn,
                expected_identity=expected_identity,
                expected_description=expected_description,
            )

    def _retrieve_source(
        self,
        url: str,
        expected_mpn: str,
        *,
        expected_identity: str | None = None,
        expected_description: str | None = None,
    ) -> RetrievalResult:
        """Fetch and verify one explicitly approved manufacturer URL."""
        payload: RetrievedPayload | None = None

        def fail(message: str, code: str = "SOURCE_RETRIEVAL_FAILED") -> RetrievalResult:
            return _failure(
                message,
                code,
                http_status=payload.status_code if payload is not None else None,
                content_type=(payload.headers.get("content-type") if payload is not None else None),
            )

        try:
            try:
                domain = _approved_domain(url, self.approved_domains)
            except ValueError as error:
                return fail(str(error), "SOURCE_DOMAIN_NOT_APPROVED")
            if not expected_mpn.strip():
                return fail("An expected MPN is required.", "MPN_MISSING")

            payload = self._fetcher(url, self.timeout)
            final_url = payload.final_url or url
            try:
                final_domain = _approved_domain(final_url, self.approved_domains)
            except ValueError as error:
                return fail(str(error), "SOURCE_REDIRECT_NOT_APPROVED")
            if payload.status_code < 200 or payload.status_code >= 300:
                return fail(f"Source returned HTTP status {payload.status_code}.", "SOURCE_HTTP_ERROR")
            if not payload.body:
                return fail("Source returned an empty response.", "SOURCE_EMPTY")

            source_type = _source_type(final_url, payload.headers, payload.body)
            if source_type is None:
                return fail("Source content is not a supported HTML page or PDF.", "SOURCE_UNSUPPORTED_TYPE")

            if source_type == "pdf":
                # PDF bytes are not a reliable text representation.  Extract
                # once and use the extracted text as the authoritative check.
                source = _pdf_source_from_bytes(final_url, final_domain, payload.body)
                mpn_in_text = _contains_exact_mpn(source.extracted_text, expected_mpn)
                if not mpn_in_text and not _contains_exact_mpn(final_url, expected_mpn):
                    return fail("Exact MPN was not found in the PDF.", "EXACT_MPN_MISMATCH")
                if not mpn_in_text and not (expected_identity or expected_description):
                    return fail(
                        "Exact MPN appears only in the URL; source identity context is required.",
                        "EXACT_MPN_MISMATCH",
                    )
                if not mpn_in_text and _contains_conflicting_identifier(
                    source.extracted_text, expected_mpn, expected_description
                ):
                    return fail("A different product identifier was found in the PDF.", "EXACT_MPN_MISMATCH")
                identity_matches = _matches_catalogue_identity(
                    source.extracted_text, expected_identity, expected_description
                )
                if not mpn_in_text and not _has_catalogue_identity_signal(
                    source.extracted_text, expected_identity, expected_description
                ):
                    return fail(
                        "The MPN was present only in the URL and the retrieved source did not provide matching product identity.",
                        "SOURCE_IDENTITY_MISMATCH",
                    )
                if _has_conflicting_identity_signal(
                    source.extracted_text, expected_identity, expected_description
                ):
                    return fail(
                        "Retrieved source identity does not match the catalogue product.",
                        "SOURCE_IDENTITY_MISMATCH",
                    )
                if (expected_identity or expected_description) and not _has_product_level_identity_evidence(
                    source.extracted_text,
                    expected_mpn,
                    expected_identity,
                    expected_description,
                ):
                    return fail(
                        "Retrieved PDF did not provide sufficient product-level identity evidence.",
                        "INSUFFICIENT_PRODUCT_IDENTITY",
                    )
                content: str | bytes = payload.body
            else:
                text, title, headings = _extract_html_text(payload.body)
                if not text:
                    return fail("HTML source did not contain readable text.", "SOURCE_EMPTY")
                mpn_in_text = _contains_exact_mpn(text, expected_mpn)
                if not mpn_in_text and not _contains_exact_mpn(final_url, expected_mpn):
                    return fail("Exact MPN was not found in the HTML source.", "EXACT_MPN_MISMATCH")
                if not mpn_in_text and not (expected_identity or expected_description):
                    return fail(
                        "Exact MPN appears only in the URL; source identity context is required.",
                        "EXACT_MPN_MISMATCH",
                    )
                if not mpn_in_text and _contains_conflicting_identifier(
                    text, expected_mpn, expected_description
                ):
                    return fail("A different product identifier was found in the HTML source.", "EXACT_MPN_MISMATCH")
                identity_matches = _matches_catalogue_identity(
                    text, expected_identity, expected_description
                )
                if not mpn_in_text and not _has_catalogue_identity_signal(
                    text, expected_identity, expected_description
                ):
                    return fail(
                        (
                            "The MPN was present only in the URL and the retrieved source did not "
                            "provide matching product identity."
                            if not mpn_in_text
                            else "Retrieved source identity does not match the catalogue product."
                        ),
                        "SOURCE_IDENTITY_MISMATCH",
                    )
                if _has_conflicting_identity_signal(text, expected_identity, expected_description):
                    return fail(
                        "Retrieved source identity does not match the catalogue product.",
                        "SOURCE_IDENTITY_MISMATCH",
                    )
                if (expected_identity or expected_description) and not _has_product_level_identity_evidence(
                    text,
                    expected_mpn,
                    expected_identity,
                    expected_description,
                    title=title,
                    headings=headings,
                ):
                    return _failure(
                        "Retrieved page did not provide sufficient product-level identity evidence.",
                        "INSUFFICIENT_PRODUCT_IDENTITY",
                    )
                source_name = title or _url_source_name(final_url)
                source = _web_source(final_url, source_name, text, headings)
                # Retain the retrieved payload so a later conversion can
                # recover the page title and heading-based locations.
                content = payload.body

            manufacturer_source = ManufacturerSource(
                url=final_url,
                source_type=source_type,
                manufacturer_domain=final_domain,
                source_name=source.source_name,
                content=content,
                exact_mpn_verified=True,
            )
            return RetrievalResult(
                success=True,
                source=manufacturer_source,
                http_status=payload.status_code,
                content_type=payload.headers.get("content-type"),
            )
        except (ValueError, ExtractionError, OSError, HTTPError, URLError, TimeoutError) as error:
            return fail(str(error) or "Source retrieval failed.")
        except Exception:
            return fail("Source retrieval failed.")

    def retrieve_sources(self, urls: list[str], expected_mpn: str) -> list[RetrievalResult]:
        """Retrieve an explicit list without performing discovery."""
        return [self.retrieve_source(url, expected_mpn) for url in urls]

    def to_normalized_source(self, source: ManufacturerSource) -> NormalizedSource:
        """Convert a verified source to the existing extraction representation."""
        if source.source_type == "pdf":
            if not isinstance(source.content, bytes):
                raise ValueError("Verified PDF content must be bytes.")
            return _pdf_source_from_bytes(
                source.url, source.manufacturer_domain, source.content
            )
        if isinstance(source.content, bytes):
            html_payload = source.content
        elif isinstance(source.content, str):
            html_payload = source.content.encode("utf-8")
        else:
            raise ValueError("Verified web content must be text or bytes.")
        text, title, headings = _extract_html_text(html_payload)
        return _web_source(
            source.url,
            source.source_name or title or _url_source_name(source.url),
            text,
            headings,
        )


def _approved_domain(url: str, approved_domains: frozenset[str]) -> str:
    parsed = urlparse(url)
    domain = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https" or not domain or domain not in approved_domains:
        raise ValueError("Source URL is not on the approved HTTPS manufacturer-domain allowlist.")
    return domain


def _fetch_url(url: str, timeout: float) -> RetrievedPayload:
    request = Request(url, headers={"User-Agent": "UniHackProductIntelligence/1.0"})
    with urlopen(request, timeout=timeout) as response:  # nosec B310: URL is allowlisted first
        headers = {key.casefold(): value for key, value in response.headers.items()}
        return RetrievedPayload(response.status, headers, response.read(), response.geturl())


def _source_type(
    url: str, headers: Mapping[str, str], body: bytes
) -> ManufacturerSourceType | None:
    content_type = headers.get("content-type", "").casefold().split(";", 1)[0].strip()
    if content_type == "application/pdf" or urlparse(url).path.casefold().endswith(".pdf"):
        return "pdf" if body.startswith(b"%PDF") or content_type == "application/pdf" else None
    if content_type in {"text/html", "application/xhtml+xml", ""}:
        return "web" if not body.startswith(b"%PDF") else None
    return None


def _contains_exact_mpn(text: str, expected_mpn: str) -> bool:
    normalized_text = normalize_mpn(text)
    normalized_mpn = normalize_mpn(expected_mpn)
    pattern = rf"(?<![a-z0-9]){re.escape(normalized_mpn)}(?![a-z0-9])"
    return re.search(pattern, normalized_text) is not None


def _matches_catalogue_identity(
    text: str,
    expected_identity: str | None,
    expected_description: str | None,
) -> bool:
    """Require an independent page signal when catalogue identity is supplied.

    The MPN check deliberately remains separate.  This helper never treats a
    URL, search title, or search snippet as evidence: it only examines the
    retrieved source text.  Existing callers that have no trusted identity
    context retain the original exact-MPN behavior.
    """
    if not expected_identity and not expected_description:
        return True

    source_tokens = _identity_tokens(text)
    identity_tokens = _identity_tokens(expected_identity or "")
    description_tokens = _identity_tokens(expected_description or "")

    if _compact_identity(expected_identity or "") and _compact_identity(expected_identity or "") in _compact_identity(text):
        return True

    identity_matches = len(source_tokens.intersection(identity_tokens))
    description_matches = len(source_tokens.intersection(description_tokens))

    # A short controlled identity such as "Hunter Fan" needs one distinctive
    # overlap; longer manufacturer names need two.  Product descriptions use
    # two overlaps so generic words alone cannot establish identity.
    identity_threshold = 1 if len(identity_tokens) <= 3 else 2
    identity_ok = bool(identity_tokens) and identity_matches >= identity_threshold
    description_threshold = 1 if len(description_tokens) <= 2 else 2
    description_ok = bool(description_tokens) and description_matches >= description_threshold
    # A page that contains only the exact MPN is still valid when the MPN is
    # present in the source body.  It provides no positive identity signal,
    # but also provides no contradictory identity signal.
    return identity_ok or description_ok or not source_tokens


def _has_catalogue_identity_signal(
    text: str,
    expected_identity: str | None,
    expected_description: str | None,
) -> bool:
    """Return whether page text positively supports the supplied identity."""
    if not expected_identity and not expected_description:
        return False
    source_tokens = _identity_tokens(text)
    identity_tokens = _identity_tokens(expected_identity or "")
    description_tokens = _identity_tokens(expected_description or "")
    compact_identity = _compact_identity(expected_identity or "")
    if compact_identity and compact_identity in _compact_identity(text):
        return True
    if len(source_tokens.intersection(identity_tokens)) >= (1 if len(identity_tokens) <= 3 else 2):
        return True
    description_threshold = 1 if len(description_tokens) <= 2 else 2
    return len(source_tokens.intersection(description_tokens)) >= description_threshold


def _has_conflicting_identity_signal(
    text: str,
    expected_identity: str | None,
    expected_description: str | None,
) -> bool:
    """Return whether non-generic page text contradicts supplied identity."""
    if not expected_identity and not expected_description:
        return False
    return bool(_identity_tokens(text)) and not _has_catalogue_identity_signal(
        text, expected_identity, expected_description
    )


def _has_product_level_identity_evidence(
    text: str,
    expected_mpn: str,
    expected_identity: str | None,
    expected_description: str | None,
    *,
    title: str = "",
    headings: list[str] | None = None,
) -> bool:
    """Require evidence that the retrieved content is a product source.

    This intentionally accepts several representations rather than requiring
    one HTML selector.  URL text is never considered.  A page must expose the
    exact MPN in a product-like title/heading or labeled model field, or
    provide coherent catalogue-description evidence.
    """
    headings = headings or []
    if any(
        _contains_exact_mpn(value, expected_mpn)
        for value in [title, *headings]
    ):
        return True

    label_pattern = (
        r"(?:model(?:\s+number)?|mpn|manufacturer\s+part\s+number|"
        r"part\s+number|sku|product\s+code|eoc)\s*[:#-]?\s*"
        + re.escape(normalize_mpn(expected_mpn))
    )
    if re.search(label_pattern, normalize_mpn(text), flags=re.IGNORECASE):
        return True

    source_tokens = _identity_tokens(text)
    description_tokens = _identity_tokens(expected_description or "")
    description_threshold = 1 if len(description_tokens) <= 2 else 2
    description_matches = len(source_tokens.intersection(description_tokens))
    if description_tokens and description_matches >= description_threshold:
        return True

    # A concise manufacturer/brand identity together with a visible exact
    # MPN is also product-level evidence, even when the page uses an unusual
    # non-heading layout.
    return _contains_exact_mpn(text, expected_mpn) and _has_catalogue_identity_signal(
        text, expected_identity, None
    )


def _contains_conflicting_identifier(
    text: str,
    expected_mpn: str,
    expected_description: str | None,
) -> bool:
    """Reject a URL-only MPN when the page names another model/part number."""
    expected = normalize_mpn(expected_mpn)
    description_identifiers = {
        normalize_mpn(token)
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]*\d[A-Za-z0-9._/-]*", expected_description or "")
    }
    for token in re.findall(r"(?<![A-Za-z0-9])[A-Za-z0-9][A-Za-z0-9._/-]*\d[A-Za-z0-9._/-]*(?![A-Za-z0-9])", text):
        normalized = normalize_mpn(token)
        if normalized != expected and normalized not in description_identifiers:
            return True
    return False


_IDENTITY_STOP_WORDS = frozenset(
    {
        "and", "the", "with", "for", "from", "model", "product", "item",
        "part", "number", "new", "blk", "ext", "int", "lowe", "arg",
        "accessories", "collection", "category", "search", "results", "page",
        "shop", "store", "products", "online", "buy", "home", "sale", "men",
        "mens", "women", "womens",
    }
)


def _identity_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) >= 3
        and any(character.isalpha() for character in token)
        and token not in _IDENTITY_STOP_WORDS
    }


def _compact_identity(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _pdf_source_from_bytes(url: str, domain: str, content: bytes) -> NormalizedSource:
    suffix = Path(urlparse(url).path).suffix or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(content)
        temporary_path = Path(handle.name)
    try:
        extracted = extract_pdf(temporary_path)
        return NormalizedSource(
            source_id=extracted.source_id,
            source_type="pdf",
            source_name=_url_source_name(url),
            extracted_text=extracted.extracted_text,
            locations=extracted.locations,
        )
    finally:
        temporary_path.unlink(missing_ok=True)


def _web_source(url: str, source_name: str, text: str, headings: list[str]) -> NormalizedSource:
    locations = [SourceLocation("document")]
    locations.extend(SourceLocation(heading) for heading in headings if heading != "document")
    source_id = hashlib.sha256((url + "\n" + text).encode("utf-8")).hexdigest()[:16]
    return NormalizedSource(
        source_id=source_id,
        source_type="web",
        source_name=source_name,
        extracted_text=text,
        locations=tuple(locations),
    )


def _url_source_name(url: str) -> str:
    path_name = Path(urlparse(url).path).name
    return path_name or urlparse(url).netloc


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.headings: list[str] = []
        self.title_parts: list[str] = []
        self.structured_parts: list[str] = []
        self._structured_chars = 0
        self._hidden_depth = 0
        self._heading_depth = 0
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value:
                for fragment in _extract_structured_identifier_fragments(value):
                    if self._structured_chars >= _MAX_STRUCTURED_TEXT_CHARS:
                        break
                    remaining = _MAX_STRUCTURED_TEXT_CHARS - self._structured_chars
                    fragment = fragment[:remaining]
                    if fragment and fragment not in self.structured_parts:
                        self.structured_parts.append(fragment)
                        self._structured_chars += len(fragment)
        if tag in {"script", "style", "noscript", "template"}:
            self._hidden_depth += 1
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_depth += 1
        if tag == "title":
            self._title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"} and self._hidden_depth:
            self._hidden_depth -= 1
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._heading_depth:
            self._heading_depth -= 1
        if tag == "title" and self._title_depth:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._hidden_depth:
            return
        value = " ".join(html.unescape(data).split())
        if not value:
            return
        self.parts.append(value)
        if self._heading_depth:
            self.headings.append(value)
        if self._title_depth:
            self.title_parts.append(value)


def _extract_html_text(payload: bytes) -> tuple[str, str, list[str]]:
    parser = _VisibleTextParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    parser.close()
    parts = [*parser.parts, *parser.structured_parts]
    return "\n".join(parts), " ".join(parser.title_parts), parser.headings


_MAX_STRUCTURED_ATTRIBUTE_CHARS = 100_000
_MAX_STRUCTURED_TEXT_CHARS = 50_000
_IDENTIFIER_KEYS = frozenset(
    {
        "eoc",
        "mpn",
        "model",
        "modelnumber",
        "modelno",
        "partnumber",
        "partno",
        "manufacturermodel",
        "manufacturerpartnumber",
        "productcode",
        "productid",
        "itemnumber",
        "itemno",
        "sku",
    }
)


def _extract_structured_identifier_fragments(raw_value: str) -> list[str]:
    """Extract only explicitly labelled product identifiers from an attribute.

    HTML attributes can contain large JSON blobs or malformed JavaScript.  The
    parser is deliberately bounded and only emits values whose key/label is a
    recognized identifier field.  Arbitrary tracking IDs and unrelated scalar
    attributes therefore cannot become MPN evidence.
    """
    decoded = html.unescape(raw_value).strip()
    if not decoded or len(decoded) > _MAX_STRUCTURED_ATTRIBUTE_CHARS:
        return []
    parsed: object | None = None
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(decoded)
            break
        except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
            continue
    if not isinstance(parsed, (dict, list)):
        return []
    fragments: list[str] = []

    def add(label: object, value: object) -> None:
        if not isinstance(label, str) or not _is_identifier_label(label):
            return
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            text = " ".join(str(value).split())
            if text:
                fragment = f"{label}: {text}"
                if fragment not in fragments:
                    fragments.append(fragment)

    def walk(value: object) -> None:
        if isinstance(value, dict):
            label = value.get("label")
            if label is None:
                label = value.get("name") if _is_identifier_label(str(value.get("name", ""))) else None
            if label is not None:
                add(label, value.get("value", value.get("values")))
            for key, child in value.items():
                if _is_identifier_label(str(key)):
                    if isinstance(child, list):
                        for item in child:
                            add(key, item)
                    else:
                        add(key, child)
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(parsed)
    return fragments


def _is_identifier_label(value: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", value.casefold())
    return compact in _IDENTIFIER_KEYS


def _failure(
    message: str,
    code: str = "SOURCE_RETRIEVAL_FAILED",
    *,
    http_status: int | None = None,
    content_type: str | None = None,
) -> RetrievalResult:
    return RetrievalResult(
        success=False,
        error=message,
        code=code,
        http_status=http_status,
        content_type=content_type,
    )
