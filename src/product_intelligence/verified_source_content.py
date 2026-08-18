"""Deterministic structured content extraction from verified sources."""

from __future__ import annotations

import re
from collections.abc import Mapping
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field

from .manufacturer_enrichment import ManufacturerSource
from .extraction import NormalizedSource


ContentSourceType = Literal["web", "pdf"]


class SourceLink(BaseModel):
    """A link found in verified source content."""

    url: str = Field(min_length=1)
    kind: Literal["link", "document", "video"] = "link"
    text: str = ""


class VerifiedSourceContent(BaseModel):
    """Structured content extracted without adding outside product facts."""

    canonical_url: str
    source_type: ContentSourceType
    source_id: str | None = None
    source_name: str | None = None
    locations: list[str] = Field(default_factory=list)
    page_title: str | None = None
    product_name: str | None = None
    manufacturer_brand_text: str | None = None
    mpn_model_text: str | None = None
    description: str | None = None
    features: list[str] = Field(default_factory=list)
    specification_text: list[str] = Field(default_factory=list)
    links: list[SourceLink] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)
    document_urls: list[str] = Field(default_factory=list)
    video_urls: list[str] = Field(default_factory=list)
    structured: "StructuredProductData" = Field(default_factory=lambda: StructuredProductData())


class StructuredProductData(BaseModel):
    """Directly labelled identifiers, measurements, and package facts."""

    upc: str | None = None
    ean: str | None = None
    gtin: str | None = None
    unspsc: str | None = None
    length: str | None = None
    length_uom: str | None = None
    height: str | None = None
    height_uom: str | None = None
    width: str | None = None
    width_uom: str | None = None
    weight: str | None = None
    weight_uom: str | None = None
    volume: str | None = None
    volume_uom: str | None = None
    warranty: str | None = None
    selling_qty: str | None = None
    selling_uom: str | None = None
    packaging_information: str | None = None


class _Element:
    def __init__(self, tag: str, context: str | None = None, url: str | None = None) -> None:
        self.tag = tag
        self.context = context
        self.url = url
        self.parts: list[str] = []

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


class _ProductContentParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.stack: list[_Element] = []
        self.page_title: str | None = None
        self.product_name: str | None = None
        self.manufacturer_brand_text: str | None = None
        self.mpn_model_text: str | None = None
        self.description: str | None = None
        self.features: list[str] = []
        self.specification_text: list[str] = []
        self.links: list[SourceLink] = []
        self.image_urls: list[str] = []
        self.document_urls: list[str] = []
        self.video_urls: list[str] = []
        self.visible_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        tokens = " ".join(
            attributes.get(key, "")
            for key in ("id", "class", "aria-label", "itemprop", "name", "property")
        ).casefold()
        parent_context = self.stack[-1].context if self.stack else None
        context = _context_for(tag, tokens, parent_context)
        element_url = urljoin(self.base_url, attributes.get("href", "")) if tag.casefold() == "a" and attributes.get("href") else None
        self.stack.append(_Element(tag.casefold(), context, element_url))

        if tag.casefold() == "meta":
            content = attributes.get("content", "").strip()
            if content:
                if "description" in tokens and self.description is None:
                    self.description = content
                elif "og:title" in tokens and self.page_title is None:
                    self.page_title = content
                elif "brand" in tokens and self.manufacturer_brand_text is None:
                    self.manufacturer_brand_text = content
                elif any(value in tokens for value in ("mpn", "model", "sku")) and self.mpn_model_text is None:
                    self.mpn_model_text = content

        if tag.casefold() == "a":
            self._add_link(attributes.get("href", ""), attributes.get("aria-label", ""))
        elif tag.casefold() == "img":
            self._add_url(self.image_urls, attributes.get("src", ""))
        elif tag.casefold() in {"video", "source"}:
            self._add_video(attributes.get("src", ""))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i].tag == tag), None)
        if index is None:
            return
        element = self.stack[index]
        text = element.text
        if text:
            if tag == "title" and self.page_title is None:
                self.page_title = text
            if tag == "h1" and self.product_name is None:
                self.product_name = text
            if _is_brand_element(tag, element.context) and self.manufacturer_brand_text is None:
                self.manufacturer_brand_text = text
            if _is_mpn_element(tag, element.context) and self.mpn_model_text is None:
                self.mpn_model_text = text
            if element.context == "description" and self.description is None:
                self.description = text
            if tag == "li" and element.context == "features":
                _append_unique(self.features, text)
            if element.context == "specifications" and tag in {"tr", "li", "dt", "dd", "p"}:
                _append_unique(self.specification_text, text)
            if tag == "a" and element.url:
                for link in self.links:
                    if link.url == element.url and not link.text:
                        link.text = text
        del self.stack[index:]

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if not clean:
            return
        _append_unique(self.visible_text, clean)
        for element in self.stack:
            element.parts.append(clean)

    def _add_link(self, raw_url: str, text: str) -> None:
        if not raw_url or raw_url.casefold().startswith(("javascript:", "mailto:", "#")):
            return
        url = urljoin(self.base_url, raw_url)
        kind = _link_kind(url)
        link = SourceLink(url=url, kind=kind, text=" ".join(text.split()))
        if not any(existing.url == url for existing in self.links):
            self.links.append(link)
        if kind == "document":
            _append_unique(self.document_urls, url)
        elif kind == "video":
            _append_unique(self.video_urls, url)

    def _add_url(self, target: list[str], raw_url: str) -> None:
        if raw_url:
            _append_unique(target, urljoin(self.base_url, raw_url))

    def _add_video(self, raw_url: str) -> None:
        if raw_url:
            url = urljoin(self.base_url, raw_url)
            _append_unique(self.video_urls, url)


def extract_verified_source_content(
    source: ManufacturerSource,
    normalized_source: NormalizedSource | None = None,
) -> VerifiedSourceContent:
    """Extract structured fields from a source already verified by the provider."""
    if not source.exact_mpn_verified:
        raise ValueError("Source content extraction requires exact-MPN verification.")
    if source.source_type == "pdf":
        if not isinstance(source.content, bytes):
            raise ValueError("Verified PDF content must be bytes.")
        from .manufacturer_enrichment import _pdf_source_from_bytes

        normalized = _pdf_source_from_bytes(source.url, source.manufacturer_domain, source.content)
        return VerifiedSourceContent(
            canonical_url=source.url,
            source_type="pdf",
            source_id=normalized_source.source_id if normalized_source else None,
            source_name=normalized_source.source_name if normalized_source else source.source_name,
            locations=[location.label for location in normalized_source.locations] if normalized_source else [],
            specification_text=[normalized.extracted_text] if normalized.extracted_text else [],
        )
    if isinstance(source.content, bytes):
        payload = source.content.decode("utf-8", errors="replace")
    elif isinstance(source.content, str):
        payload = source.content
    else:
        raise ValueError("Verified web content must be text or bytes.")
    parser = _ProductContentParser(source.url)
    parser.feed(payload)
    parser.close()
    structured = _extract_structured_data("\n".join(parser.visible_text + parser.specification_text))
    return VerifiedSourceContent(
        canonical_url=source.url,
        source_type="web",
        source_id=normalized_source.source_id if normalized_source else None,
        source_name=normalized_source.source_name if normalized_source else source.source_name,
        locations=[location.label for location in normalized_source.locations] if normalized_source else [],
        page_title=parser.page_title,
        product_name=parser.product_name,
        manufacturer_brand_text=parser.manufacturer_brand_text,
        mpn_model_text=parser.mpn_model_text,
        description=parser.description,
        features=parser.features,
        specification_text=parser.specification_text,
        links=parser.links,
        image_urls=parser.image_urls,
        document_urls=parser.document_urls,
        video_urls=parser.video_urls,
        structured=structured,
    )


_MEASUREMENT_PATTERN = re.compile(
    r"(?i)\b(?P<label>length|height|width|weight|volume)\s*[:=-]\s*"
    r"(?P<value>[-+]?\d+(?:\.\d+)?)\s*(?P<uom>mm|cm|m|km|in(?:ch(?:es)?)?|ft|feet|foot|mg|g|kg|lb(?:s)?|ml|l|gal)\b"
)
_IDENTIFIER_PATTERNS = {
    "upc": re.compile(r"(?i)\bUPC\s*[:#-]?\s*([0-9][0-9 -]{5,})\b"),
    "ean": re.compile(r"(?i)\bEAN\s*[:#-]?\s*([0-9][0-9 -]{7,})\b"),
    "gtin": re.compile(r"(?i)\bGTIN\s*[:#-]?\s*([0-9][0-9 -]{7,})\b"),
    "unspsc": re.compile(r"(?i)\bUNSPSC\s*[:#-]?\s*([0-9][0-9 -]{7,})\b"),
}


def _extract_structured_data(text: str) -> StructuredProductData:
    """Extract only facts with explicit, deterministic field labels."""
    values: dict[str, str | None] = {}
    for name, pattern in _IDENTIFIER_PATTERNS.items():
        match = pattern.search(text)
        if match:
            values[name] = _compact_identifier(match.group(1))

    for match in _MEASUREMENT_PATTERN.finditer(text):
        label = match.group("label").casefold()
        if label not in values:
            values[label] = match.group("value")
            values[f"{label}_uom"] = match.group("uom")

    values["warranty"] = _labelled_text(text, "warranty")
    values["selling_qty"] = _labelled_text(text, "selling quantity|selling qty|pack quantity|package quantity")
    if values.get("selling_qty"):
        quantity_match = re.match(r"(?i)\s*([0-9]+(?:\.\d+)?)\s*(.*)", values["selling_qty"] or "")
        if quantity_match:
            values["selling_qty"] = quantity_match.group(1)
            values["selling_uom"] = quantity_match.group(2).strip() or None
    values["packaging_information"] = _labelled_text(
        text, "standard packaging information|packaging information|packaging"
    )
    return StructuredProductData(**values)


def _labelled_text(text: str, labels: str) -> str | None:
    match = re.search(rf"(?im)^\s*(?:{labels})\s*[:=-]\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else None


def _compact_identifier(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("-", "")


def _context_for(tag: str, tokens: str, parent: str | None) -> str | None:
    if parent in {"features", "specifications", "description"}:
        return parent
    if any(token in tokens for token in ("feature", "benefit")):
        return "features"
    if any(token in tokens for token in ("specification", "specs", "technical")):
        return "specifications"
    if any(token in tokens for token in ("description", "product-description")):
        return "description"
    if "brand" in tokens or "manufacturer" in tokens:
        return "brand"
    if any(token in tokens for token in ("mpn", "model", "sku")):
        return "mpn"
    return None


def _is_brand_element(tag: str, context: str | None) -> bool:
    return context == "brand" or tag in {"brand"}


def _is_mpn_element(tag: str, context: str | None) -> bool:
    return context == "mpn" or tag in {"mpn", "model"}


def _link_kind(url: str) -> Literal["link", "document", "video"]:
    parsed = urlparse(url)
    suffix = PurePosixPath(parsed.path.casefold()).suffix
    if suffix in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv"}:
        return "document"
    if suffix in {".mp4", ".webm", ".mov", ".avi"} or any(
        host in parsed.netloc.casefold() for host in ("youtube.", "youtu.be", "vimeo.")
    ):
        return "video"
    return "link"


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
