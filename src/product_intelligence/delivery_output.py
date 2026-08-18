"""Deterministic delivery-row mapping and comparison helpers."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .catalog_input import CatalogInputRow
from .delivery_schema import DeliverySchema, DeliverySchemaError
from .verified_source_content import SourceLink, VerifiedSourceContent
from .reference_data import UOMReference


class DeliveryFieldDifference(BaseModel):
    """One exact field difference between generated and known-good rows."""

    field: str
    generated: str
    expected: str


class DeliveryComparison(BaseModel):
    """Deterministic comparison summary for one catalogue row."""

    mfg_part_num: str
    matches: bool
    differences: list[DeliveryFieldDifference] = Field(default_factory=list)


class DeliveryFieldEvidence(BaseModel):
    """Provenance retained for one populated factual delivery field."""

    field: str
    value: str
    source_id: str
    source_name: str
    location: str
    quote: str
    source_url: str


def map_raw_fields_to_delivery(
    input_row: CatalogInputRow,
    schema: DeliverySchema,
) -> dict[str, str]:
    """Copy only the six raw fields that also exist in the delivery schema."""
    delivery_row = schema.empty_row()
    for field, value in input_row.raw_fields().items():
        if field not in delivery_row:
            raise DeliverySchemaError(f"Delivery schema is missing raw field '{field}'.")
        delivery_row[field] = value
    return schema.validate_row(delivery_row)


def map_verified_source_content_to_delivery(
    delivery_row: dict[str, str],
    content: VerifiedSourceContent,
    schema: DeliverySchema,
    uom_reference: UOMReference | None = None,
    provenance: dict[str, DeliveryFieldEvidence] | None = None,
) -> dict[str, str]:
    """Map only explicitly extracted source content into known delivery fields.

    This mapper is deliberately conservative.  It does not infer product facts;
    it copies parsed URLs, descriptions, features, and labels from a source that
    has already passed exact-MPN verification.  Existing non-empty values win so
    that a second source cannot silently overwrite the first source's content.
    """
    _set_if_present(delivery_row, schema, "MFR URL", content.canonical_url)
    _set_if_present(delivery_row, schema, "Product Name", content.product_name)
    _set_if_present(delivery_row, schema, "MARKETING_DESCRIPTION", content.description)

    for index, feature in enumerate(content.features[:20], start=1):
        _set_if_present(delivery_row, schema, f"ITEM_FEATURES_{index}", feature)

    for index, link in enumerate(content.links[:5], start=1):
        _set_if_present(delivery_row, schema, f"Ref URL {index}", link.url)

    if content.image_urls:
        _set_if_present(delivery_row, schema, "Product Image", content.image_urls[0])
        for index, image_url in enumerate(content.image_urls[1:5], start=1):
            _set_if_present(delivery_row, schema, f"Alternate Image {index}", image_url)
        _set_if_present(delivery_row, schema, "Actual Image (Yes/No)", "Yes")

    if content.source_type == "pdf":
        _set_if_present(delivery_row, schema, "Specification Sheet", content.canonical_url)

    document_counts: dict[str, int] = {}
    for link in content.links:
        if link.kind == "video":
            continue
        field = _document_field(link)
        if field is not None:
            occurrence = document_counts.get(field, 0)
            document_counts[field] = occurrence + 1
            target = field
            if field == "SDS" and occurrence > 0:
                target = "SDS_1"
            _set_if_present(delivery_row, schema, target, link.url)

    for index, video_url in enumerate(content.video_urls[:2]):
        _set_if_present(delivery_row, schema, "Video Link" if index == 0 else "Video Link 1", video_url)

    _map_structured_fields(delivery_row, content, schema, uom_reference)
    if provenance is not None:
        _record_content_provenance(delivery_row, content, schema, provenance)

    return schema.validate_row(delivery_row)


def _set_if_present(
    delivery_row: dict[str, str], schema: DeliverySchema, field: str, value: str | None
) -> None:
    if field in schema.columns and value and not delivery_row.get(field):
        delivery_row[field] = value


def _document_field(link: SourceLink) -> str | None:
    """Classify a document link using explicit filename/anchor terms only."""
    if link.kind != "document":
        return None
    parsed = urlparse(link.url)
    text = f"{link.text} {parsed.path}".casefold().replace("_", " ").replace("-", " ")
    rules = (
        (("sds", "safety data", "msds"), "SDS"),
        (("catalog", "catalogue"), "Catalog"),
        (("specification", "spec", "technical data"), "Specification Sheet"),
        (("installation", "instruction"), "Instruction/Installation Manual"),
        (("service",), "Service Manual"),
        (("owner", "user"), "Owners/User Manual"),
        (("line drawing",), "Line Drawing"),
        (("rohs",), "RoHS"),
        (("engineering drawing",), "Full Engineering Drawing"),
        (("technical bulletin",), "Technical Bulletin"),
        (("submittal",), "Submittal"),
        (("compatibility",), "Compatibility Chart"),
    )
    for tokens, field in rules:
        if any(token in text for token in tokens):
            return field
    return None


def _map_structured_fields(
    delivery_row: dict[str, str],
    content: VerifiedSourceContent,
    schema: DeliverySchema,
    uom_reference: UOMReference | None,
) -> None:
    structured = content.structured
    for source_name, delivery_name in (
        ("upc", "UPC"),
        ("ean", "EAN"),
        ("gtin", "GTIN"),
        ("unspsc", "UNSPSC"),
        ("warranty", "Warranty"),
        ("packaging_information", "Standard Packaging Information"),
    ):
        _set_if_present(delivery_row, schema, delivery_name, getattr(structured, source_name))

    for dimension in ("length", "height", "width", "weight", "volume"):
        value = getattr(structured, dimension)
        uom = getattr(structured, f"{dimension}_uom")
        resolved_uom = _resolve_uom(uom, uom_reference)
        if value and resolved_uom:
            _set_if_present(delivery_row, schema, dimension.upper(), value)
            _set_if_present(delivery_row, schema, f"{dimension.upper()}_UOM", resolved_uom)

    selling_uom = _resolve_uom(structured.selling_uom, uom_reference)
    if structured.selling_qty and selling_uom:
        _set_if_present(delivery_row, schema, "Selling Qty", structured.selling_qty)
        _set_if_present(delivery_row, schema, "Selling UOM", selling_uom)


def _resolve_uom(value: str | None, reference: UOMReference | None) -> str | None:
    if not value or reference is None:
        return None
    result = reference.resolve(value)
    if result.status != "resolved" or not isinstance(result.resolved_value, str):
        return None
    return result.resolved_value


def _record_content_provenance(
    delivery_row: dict[str, str],
    content: VerifiedSourceContent,
    schema: DeliverySchema,
    provenance: dict[str, DeliveryFieldEvidence],
) -> None:
    source_id = content.source_id or content.canonical_url
    source_name = content.source_name or content.canonical_url
    location = content.locations[0] if content.locations else "document"

    def record(field: str, quote: str | None = None) -> None:
        value = delivery_row.get(field, "")
        if field not in schema.columns or not value or field in provenance:
            return
        provenance[field] = DeliveryFieldEvidence(
            field=field,
            value=value,
            source_id=source_id,
            source_name=source_name,
            location=location,
            quote=quote or value,
            source_url=content.canonical_url,
        )

    for field, quote in (
        ("MFR URL", content.canonical_url),
        ("Product Name", content.product_name),
        ("MARKETING_DESCRIPTION", content.description),
        ("UPC", content.structured.upc),
        ("EAN", content.structured.ean),
        ("GTIN", content.structured.gtin),
        ("UNSPSC", content.structured.unspsc),
        ("Warranty", content.structured.warranty),
        ("Standard Packaging Information", content.structured.packaging_information),
    ):
        if quote:
            record(field, quote)

    for index, feature in enumerate(content.features[:20], start=1):
        record(f"ITEM_FEATURES_{index}", feature)
    for index, link in enumerate(content.links[:5], start=1):
        record(f"Ref URL {index}", link.text or link.url)
    if content.image_urls:
        record("Product Image", content.image_urls[0])
        for index, image_url in enumerate(content.image_urls[1:5], start=1):
            record(f"Alternate Image {index}", image_url)
        record("Actual Image (Yes/No)", "Image URL was present in the verified source.")
    for field, links in _document_links(content):
        for link in links:
            target = field
            if field == "SDS" and target in provenance:
                target = "SDS_1"
            record(target, link.text or link.url)
    for index, video_url in enumerate(content.video_urls[:2]):
        record("Video Link" if index == 0 else "Video Link 1", video_url)
    for dimension in ("length", "height", "width", "weight", "volume"):
        value = getattr(content.structured, dimension)
        uom = getattr(content.structured, f"{dimension}_uom")
        if value and uom:
            record(dimension.upper(), f"{dimension.title()}: {value} {uom}")
            record(f"{dimension.upper()}_UOM", uom)
    if content.structured.selling_qty and content.structured.selling_uom:
        record("Selling Qty", f"Selling Quantity: {content.structured.selling_qty} {content.structured.selling_uom}")
        record("Selling UOM", content.structured.selling_uom)


def _document_links(content: VerifiedSourceContent) -> list[tuple[str, list[SourceLink]]]:
    groups: dict[str, list[SourceLink]] = {}
    for link in content.links:
        field = _document_field(link)
        if field is not None:
            groups.setdefault(field, []).append(link)
    return list(groups.items())


def compare_delivery_rows(
    generated: Mapping[str, str],
    expected: Mapping[str, str],
    schema: DeliverySchema,
) -> DeliveryComparison:
    """Compare every delivery field in schema order without modifying either row."""
    generated_row = schema.validate_row(generated)
    expected_row = schema.validate_row(expected)
    differences = [
        DeliveryFieldDifference(
            field=column,
            generated=generated_row[column],
            expected=expected_row[column],
        )
        for column in schema.columns
        if generated_row[column] != expected_row[column]
    ]
    return DeliveryComparison(
        mfg_part_num=generated_row.get("Mfg_Part_Num", ""),
        matches=not differences,
        differences=differences,
    )
