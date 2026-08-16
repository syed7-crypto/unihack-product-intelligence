"""Controlled retrieval and verification of approved manufacturer sources.

This module deliberately does not discover URLs. Callers must provide an
explicitly approved URL, and the exact manufacturer part number must occur in
the retrieved source before a ``NormalizedSource`` is created.
"""

from __future__ import annotations

import hashlib
import html
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
    ) -> None:
        self.approved_domains = frozenset(
            domain.casefold().rstrip(".")
            for domain in (approved_domains or DEFAULT_APPROVED_DOMAINS)
        )
        self.timeout = timeout
        self._fetcher = fetcher or _fetch_url

    def retrieve_source(self, url: str, expected_mpn: str) -> RetrievalResult:
        """Fetch and verify one explicitly approved manufacturer URL."""
        try:
            domain = _approved_domain(url, self.approved_domains)
            if not expected_mpn.strip():
                return _failure("An expected MPN is required.")

            payload = self._fetcher(url, self.timeout)
            if payload.status_code < 200 or payload.status_code >= 300:
                return _failure(f"Source returned HTTP status {payload.status_code}.")
            if not payload.body:
                return _failure("Source returned an empty response.")

            source_type = _source_type(url, payload.headers, payload.body)
            if source_type is None:
                return _failure("Source content is not a supported HTML page or PDF.")

            if source_type == "pdf":
                content_for_check = payload.body.decode("latin-1", errors="ignore")
                if not _contains_exact_mpn(content_for_check, expected_mpn):
                    # PDF text is not reliably represented in raw bytes. The
                    # existing PDF extractor is the authoritative text check.
                    source = _pdf_source_from_bytes(url, domain, payload.body)
                    if not _contains_exact_mpn(source.extracted_text, expected_mpn):
                        return _failure("Exact MPN was not found in the PDF.")
                else:
                    source = _pdf_source_from_bytes(url, domain, payload.body)
                    if not _contains_exact_mpn(source.extracted_text, expected_mpn):
                        return _failure("Exact MPN was not found in the PDF text.")
                content: str | bytes = payload.body
            else:
                text, title, headings = _extract_html_text(payload.body)
                if not text:
                    return _failure("HTML source did not contain readable text.")
                if not _contains_exact_mpn(text, expected_mpn):
                    return _failure("Exact MPN was not found in the HTML source.")
                source_name = title or _url_source_name(url)
                source = _web_source(url, source_name, text, headings)
                # Retain the retrieved payload so a later conversion can
                # recover the page title and heading-based locations.
                content = payload.body

            manufacturer_source = ManufacturerSource(
                url=url,
                source_type=source_type,
                manufacturer_domain=domain,
                source_name=source.source_name,
                content=content,
                exact_mpn_verified=True,
            )
            return RetrievalResult(success=True, source=manufacturer_source)
        except (ValueError, ExtractionError, OSError, HTTPError, URLError, TimeoutError) as error:
            return _failure(str(error) or "Source retrieval failed.")
        except Exception:
            return _failure("Source retrieval failed.")

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
        return RetrievedPayload(response.status, headers, response.read())


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
    pattern = rf"(?<![A-Za-z0-9]){re.escape(expected_mpn.strip())}(?![A-Za-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


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
        self._hidden_depth = 0
        self._heading_depth = 0
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
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
    return "\n".join(parser.parts), " ".join(parser.title_parts), parser.headings


def _failure(message: str) -> RetrievalResult:
    return RetrievalResult(success=False, error=message)
