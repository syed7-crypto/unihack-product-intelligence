"""Generic catalogue-row enrichment orchestration.

This module connects the existing catalogue, manufacturer-source, product
intelligence, reference, and delivery layers. It does not discover sources or
invent missing reference data.
"""

from __future__ import annotations

import re
from contextlib import nullcontext
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .catalog_input import CatalogInputRow, brand_candidate
from .controlled_attribute_mapping import (
    ControlledAttributeMapping,
    ControlledAttributeMappingRegistry,
)
from .delivery_output import (
    compare_delivery_rows,
    DeliveryFieldEvidence,
    map_raw_fields_to_delivery,
    map_verified_source_content_to_delivery,
)
from .delivery_schema import DeliverySchema
from .manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    ManufacturerSource,
    RetrievalResult,
)
from .verified_source_content import extract_verified_source_content
from .pipeline import ProductIntelligencePipelineError, ProductIntelligenceResult, run_pipeline
from .reference_data import (
    AttributeReference,
    BrandReference,
    BrandManufacturerReference,
    CatalogReferenceResolution,
    IdentityAssertion,
    ManufacturerReference,
    ReferenceResolutionResult,
    UOMReference,
    normalize_reference_value,
)
from .runtime_timing import RuntimeTimingAccumulator
from .review import ReviewReport, build_review_report
from .runtime_policy import (
    CandidateTelemetry,
    IdentityResolutionResult,
    _catalogue_identity_conflict,
)


AttributeDeliveryMapping = ControlledAttributeMapping
AttributeDeliveryMappings = ControlledAttributeMappingRegistry
MAX_DELIVERY_ATTRIBUTE_SLOTS = 50


class EnrichmentSourceDiagnostic(BaseModel):
    """Outcome of retrieving and normalizing one explicit source URL."""

    url: str
    success: bool
    source_type: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    exact_mpn_verified: bool = False
    error: str | None = None
    code: str | None = None


class MappingDiagnostic(BaseModel):
    """Reason an attribute mapping was populated or safely left blank."""

    attribute_name: str
    slot: int | None = None
    status: Literal["mapped", "skipped"]
    reason: str
    code: str | None = None


class EvaluationFieldDifference(BaseModel):
    """A generated-vs-expected difference, explicitly separate from evidence."""

    column: str
    generated_value: str
    expected_value: str
    review_required: bool = True


class EvaluationComparison(BaseModel):
    """Evaluation-only comparison against a supplied known-good row."""

    mfg_part_num: str
    matches: bool
    differences: list[EvaluationFieldDifference] = Field(default_factory=list)


class CatalogueEnrichmentResult(BaseModel):
    """Result of enriching one catalogue row, including safe partial failures."""

    catalogue_row: CatalogInputRow
    pipeline_result: ProductIntelligenceResult | None
    delivery_row: dict[str, str]
    delivery_evidence: dict[str, DeliveryFieldEvidence] = Field(default_factory=dict)
    source_diagnostics: list[EnrichmentSourceDiagnostic] = Field(default_factory=list)
    reference_resolution: CatalogReferenceResolution | None = None
    mapping_diagnostics: list[MappingDiagnostic] = Field(default_factory=list)
    evaluation_comparison: EvaluationComparison | None = None
    candidate_telemetry: list[CandidateTelemetry] = Field(default_factory=list)
    review: ReviewReport = Field(default_factory=ReviewReport)


class CatalogueEnrichmentError(RuntimeError):
    """Raised for invalid orchestration inputs, not ordinary source failures."""


def enrich_catalogue_row(
    catalogue_row: CatalogInputRow,
    source_urls: Sequence[str],
    delivery_schema: DeliverySchema,
    *,
    client=None,
    provider: ManufacturerEnrichmentProvider | None = None,
    manufacturer_reference: ManufacturerReference | None = None,
    brand_reference: BrandReference | None = None,
    brand_manufacturer_reference: BrandManufacturerReference | None = None,
    attribute_reference: AttributeReference | None = None,
    uom_reference: UOMReference | None = None,
    attribute_mappings: AttributeDeliveryMappings | None = None,
    expected_delivery_row: dict[str, str] | None = None,
    verified_sources: Sequence[ManufacturerSource] | None = None,
    initial_source_diagnostics: Sequence[EnrichmentSourceDiagnostic] = (),
    runtime_identity: IdentityResolutionResult | None = None,
    runtime_timing: RuntimeTimingAccumulator | None = None,
    attribute_extraction_concurrency: int = 1,
) -> CatalogueEnrichmentResult:
    """Enrich one row using only explicit, exact-MPN-verified sources.

    Retrieval failures are retained in diagnostics. If every source fails,
    the result contains only the preserved raw catalogue fields and no
    pipeline result or enrichment values.
    """
    if not source_urls and verified_sources is None:
        raise CatalogueEnrichmentError("At least one explicit source URL is required.")

    provider = provider or ManufacturerEnrichmentProvider(runtime_timing=runtime_timing)
    if (
        runtime_timing is not None
        and isinstance(provider, ManufacturerEnrichmentProvider)
    ):
        provider = provider.with_runtime_timing(runtime_timing)
    mappings = attribute_mappings or AttributeDeliveryMappings()
    delivery_row = map_raw_fields_to_delivery(catalogue_row, delivery_schema)
    reference_resolution = _resolve_references(
        catalogue_row,
        manufacturer_reference,
        brand_reference,
        runtime_identity,
        brand_manufacturer_reference,
    )
    _map_resolved_identity(delivery_row, reference_resolution)

    normalized_sources = []
    verified_source_contents = []
    verified_source_identities: list[tuple[ManufacturerSource, str, str]] = []
    delivery_evidence: dict[str, DeliveryFieldEvidence] = {}
    diagnostics: list[EnrichmentSourceDiagnostic] = list(initial_source_diagnostics)

    for source in verified_sources or ():
        if not source.exact_mpn_verified:
            diagnostics.append(
                EnrichmentSourceDiagnostic(
                    url=source.url,
                    success=False,
                    source_type=source.source_type,
                    source_name=source.source_name,
                    code="SOURCE_NOT_EXACT_MPN_VERIFIED",
                    error="Source was not marked as exact-MPN-verified.",
                )
            )
            continue
        try:
            normalized = provider.to_normalized_source(source)
        except Exception as error:
            diagnostics.append(
                EnrichmentSourceDiagnostic(
                    url=source.url,
                    success=False,
                    source_type=source.source_type,
                    source_name=source.source_name,
                    exact_mpn_verified=source.exact_mpn_verified,
                    code="SOURCE_NORMALIZATION_FAILED",
                    error=f"Source normalization failed: {error}",
                )
            )
            continue
        normalized_sources.append(normalized)
        identity = _extract_verified_source_identity(normalized.extracted_text)
        if identity is not None:
            verified_source_identities.append((source, identity[0], identity[1]))
        try:
            verified_source_contents.append(extract_verified_source_content(source, normalized))
        except Exception as error:
            diagnostics.append(
                EnrichmentSourceDiagnostic(
                    url=source.url,
                    success=False,
                    source_type=source.source_type,
                    source_name=source.source_name,
                    exact_mpn_verified=source.exact_mpn_verified,
                    code="SOURCE_CONTENT_EXTRACTION_FAILED",
                    error=f"Verified-source content extraction failed: {error}",
                )
            )
        diagnostics.append(
            EnrichmentSourceDiagnostic(
                url=source.url,
                success=True,
                source_type=source.source_type,
                source_id=normalized.source_id,
                source_name=normalized.source_name,
                exact_mpn_verified=source.exact_mpn_verified,
            )
        )

    for url in source_urls:
        if isinstance(provider, ManufacturerEnrichmentProvider):
            # Catalogue identity is the expected product signal.  Do not use
            # a runtime candidate identity as the sole expectation: that
            # identity is independently verified only after retrieval and
            # must not be allowed to validate the page that proposed it.
            trusted_identity = next(
                (
                    candidate
                    for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand")
                    if (candidate := brand_candidate(getattr(catalogue_row, field)))
                ),
                None,
            )
            if trusted_identity is None and runtime_identity is not None and runtime_identity.state == "resolvable":
                trusted_identity = runtime_identity.resolved_identity
            if trusted_identity is None and reference_resolution is not None:
                if reference_resolution.manufacturer.status == "resolved":
                    trusted_identity = reference_resolution.manufacturer.resolved_value
                elif reference_resolution.brand.status == "resolved":
                    trusted_identity = reference_resolution.brand.resolved_value
            retrieval = provider.retrieve_source(
                url,
                catalogue_row.Mfg_Part_Num,
                expected_identity=trusted_identity,
                expected_description=catalogue_row.Part_Desc,
            )
        else:
            # Preserve compatibility with deterministic test doubles and
            # caller-provided providers using the original two-argument API.
            retrieval = provider.retrieve_source(url, catalogue_row.Mfg_Part_Num)
        if not retrieval.success or retrieval.source is None:
            diagnostics.append(
                EnrichmentSourceDiagnostic(
                    url=url,
                    success=False,
                    error=retrieval.error or "Source retrieval failed.",
                    code=retrieval.code or "SOURCE_RETRIEVAL_FAILED",
                )
            )
            continue
        source = retrieval.source
        if not source.exact_mpn_verified:
            diagnostics.append(
                EnrichmentSourceDiagnostic(
                    url=url,
                    success=False,
                    source_type=source.source_type,
                    source_name=source.source_name,
                    code="SOURCE_NOT_EXACT_MPN_VERIFIED",
                    error="Source was not marked as exact-MPN-verified.",
                )
            )
            continue
        try:
            normalized = provider.to_normalized_source(source)
        except Exception as error:
            diagnostics.append(
                EnrichmentSourceDiagnostic(
                    url=url,
                    success=False,
                    source_type=source.source_type,
                    source_name=source.source_name,
                    exact_mpn_verified=source.exact_mpn_verified,
                    code="SOURCE_NORMALIZATION_FAILED",
                    error=f"Source normalization failed: {error}",
                )
            )
            continue
        normalized_sources.append(normalized)
        identity = _extract_verified_source_identity(normalized.extracted_text)
        if identity is not None:
            verified_source_identities.append((source, identity[0], identity[1]))
        try:
            verified_source_contents.append(extract_verified_source_content(source, normalized))
        except Exception as error:
            diagnostics.append(
                EnrichmentSourceDiagnostic(
                    url=url,
                    success=False,
                    source_type=source.source_type,
                    source_name=source.source_name,
                    exact_mpn_verified=source.exact_mpn_verified,
                    code="SOURCE_CONTENT_EXTRACTION_FAILED",
                    error=f"Verified-source content extraction failed: {error}",
                )
            )
        diagnostics.append(
            EnrichmentSourceDiagnostic(
                url=url,
                success=True,
                source_type=source.source_type,
                source_id=normalized.source_id,
                source_name=normalized.source_name,
                exact_mpn_verified=source.exact_mpn_verified,
            )
        )

    source_identity = _trusted_identity_from_verified_sources(
        catalogue_row,
        verified_source_identities,
    )
    if source_identity is None and verified_source_identities:
        identity_values = {
            (normalize_reference_value(value), kind)
            for _source, value, kind in verified_source_identities
        }
        conflict = _catalogue_identity_conflict(
            catalogue_row,
            verified_source_identities[0][1],
        )
        if len(identity_values) > 1 or conflict is not None:
            diagnostics.append(
                EnrichmentSourceDiagnostic(
                    url=verified_source_identities[0][0].url,
                    success=False,
                    source_type=verified_source_identities[0][0].source_type,
                    source_name=verified_source_identities[0][0].source_name,
                    exact_mpn_verified=True,
                    code="MANUFACTURER_IDENTITY_CONFLICT",
                    error=(
                        conflict
                        or "Verified sources provided different manufacturer/brand identities."
                    ),
                )
            )
    if runtime_identity is None and source_identity is not None:
        identity_value, identity_kind, identity_source = source_identity
        runtime_identity = IdentityResolutionResult(
            state="resolvable",
            resolved_identity=identity_value,
            identity_kind=identity_kind,
            approved_domains=(identity_source.manufacturer_domain,),
            reason=(
                "Trusted manufacturer/brand identity was extracted from the "
                "verified source page; it is scoped to this row."
            ),
            verified_sources=[identity_source],
        )
        reference_resolution = _resolve_references(
            catalogue_row,
            manufacturer_reference,
            brand_reference,
            runtime_identity,
        )
        _map_resolved_identity(delivery_row, reference_resolution)

    mapping_context = (
        runtime_timing.measure("validation_delivery_mapping_duration_seconds")
        if runtime_timing is not None
        else nullcontext()
    )
    with mapping_context:
        _map_verified_source_metadata(delivery_row, diagnostics, catalogue_row)
        for content in verified_source_contents:
            map_verified_source_content_to_delivery(
                delivery_row,
                content,
                delivery_schema,
                uom_reference=uom_reference,
                provenance=delivery_evidence,
            )

    if not normalized_sources:
        return _finish_result(
            catalogue_row,
            delivery_row,
            diagnostics,
            reference_resolution,
            [],
            None,
            expected_delivery_row,
            delivery_schema,
            delivery_evidence,
        )

    try:
        pipeline_result = run_pipeline(
            normalized_sources,
            client=client,
            runtime_timing=runtime_timing,
            attribute_extraction_concurrency=attribute_extraction_concurrency,
        )
    except ProductIntelligencePipelineError as error:
        inner_diagnostics = "; ".join(
            f"{diagnostic.code}: {diagnostic.message}"
            for diagnostic in error.diagnostics
        )
        diagnostic_message = f"Pipeline failed after source verification: {error}"
        if inner_diagnostics:
            diagnostic_message += f" Inner diagnostic: {inner_diagnostics}"
        diagnostics.append(
            EnrichmentSourceDiagnostic(
                url=";".join(source_urls),
                success=False,
                code=error.code,
                error=diagnostic_message,
            )
        )
        return _finish_result(
            catalogue_row,
            delivery_row,
            diagnostics,
            reference_resolution,
            [],
            None,
            expected_delivery_row,
            delivery_schema,
            delivery_evidence,
        )
    except Exception as error:
        diagnostics.append(
            EnrichmentSourceDiagnostic(
                url=";".join(source_urls),
                success=False,
                code="PIPELINE_FAILED",
                error=f"Pipeline failed after source verification: {error}",
            )
        )
        return _finish_result(
            catalogue_row,
            delivery_row,
            diagnostics,
            reference_resolution,
            [],
            None,
            expected_delivery_row,
            delivery_schema,
            delivery_evidence,
        )

    mapping_context = (
        runtime_timing.measure("validation_delivery_mapping_duration_seconds")
        if runtime_timing is not None
        else nullcontext()
    )
    with mapping_context:
        mapping_diagnostics = _map_validated_attributes(
            delivery_row,
            pipeline_result,
            mappings,
            attribute_reference,
            uom_reference,
        )
    return _finish_result(
        catalogue_row,
        delivery_row,
        diagnostics,
        reference_resolution,
        mapping_diagnostics,
        pipeline_result,
        expected_delivery_row,
        delivery_schema,
        delivery_evidence,
    )


def _resolve_references(
    row: CatalogInputRow,
    manufacturer_reference: ManufacturerReference | None,
    brand_reference: BrandReference | None,
    runtime_identity: IdentityResolutionResult | None = None,
    brand_manufacturer_reference: BrandManufacturerReference | None = None,
) -> CatalogReferenceResolution:
    if manufacturer_reference is None:
        manufacturer = _unresolved("manufacturer", "No manufacturer reference was configured.")
    else:
        manufacturer = manufacturer_reference.resolve(row.Part_Manuf)

    brands: dict[str, ReferenceResolutionResult] = {}
    identity_assertions: list[IdentityAssertion] = []
    manufacturer_assertion: IdentityAssertion | None = None
    brand_assertions: dict[str, IdentityAssertion] = {}

    if manufacturer.status == "resolved" and isinstance(manufacturer.resolved_value, str):
        manufacturer_assertion = IdentityAssertion(
            value=manufacturer.resolved_value,
            kind="manufacturer",
            source="controlled_reference",
            trust_level="high",
        )
        identity_assertions.append(manufacturer_assertion)
    for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand"):
        candidate = brand_candidate(getattr(row, field))
        if brand_reference is None:
            result = _unresolved("brand", "No brand reference was configured.")
        else:
            result = brand_reference.resolve(candidate)
        if candidate is None:
            result = result.model_copy(update={"input_value": getattr(row, field)})
        brands[field] = result

        if result.status == "resolved" and isinstance(result.resolved_value, str):
            assertion = IdentityAssertion(
                value=result.resolved_value,
                kind="brand",
                source="controlled_reference",
                trust_level="high",
            )
            brand_assertions[field] = assertion
            identity_assertions.append(assertion)
        elif candidate:
            # Raw catalogue brands remain low-trust hints and are never used
            # as approved manufacturer identity.
            assertion = IdentityAssertion(
                value=candidate,
                kind="brand",
                source="catalogue",
                trust_level="low",
            )
            brand_assertions[field] = assertion
            identity_assertions.append(assertion)

    trusted_runtime = None
    if (
        runtime_identity is not None
        and runtime_identity.state in {"known", "resolvable"}
        and runtime_identity.resolved_identity
        and runtime_identity.identity_kind
    ):
        trusted_runtime = ReferenceResolutionResult(
            input_value=runtime_identity.resolved_identity,
            resolved_value=runtime_identity.resolved_identity,
            status="resolved",
            reference_type=runtime_identity.identity_kind,
            reason=(
                "Resolved from the current row's verified identity/source "
                "resolution; not persisted to the reference registry."
            ),
        )
        runtime_assertion = runtime_identity.identity_assertion or IdentityAssertion(
            value=runtime_identity.resolved_identity,
            kind=runtime_identity.identity_kind,
            source=("controlled_reference" if runtime_identity.state == "known" else "page_evidence"),
            trust_level="high",
        )
        identity_assertions.append(runtime_assertion)
        if runtime_identity.identity_kind == "manufacturer":
            manufacturer = trusted_runtime
            manufacturer_assertion = runtime_assertion
        else:
            brand_assertions["runtime_identity"] = runtime_assertion

    if manufacturer.status != "resolved" and brand_manufacturer_reference is not None:
        relationship_brand = next(
            (
                assertion.value
                for assertion in brand_assertions.values()
                if assertion.kind == "brand"
            ),
            None,
        )
        relationship = brand_manufacturer_reference.resolve(relationship_brand)
        if relationship.status == "resolved" and isinstance(relationship.resolved_value, str):
            manufacturer = relationship.model_copy(update={"reference_type": "manufacturer"})
            manufacturer_assertion = IdentityAssertion(
                value=relationship.resolved_value,
                kind="manufacturer",
                source="controlled_reference",
                trust_level="high",
            )
            identity_assertions.append(manufacturer_assertion)

    return CatalogReferenceResolution(
        manufacturer=manufacturer,
        brands=brands,
        runtime_identity=trusted_runtime,
        identity_assertions=identity_assertions,
        manufacturer_assertion=manufacturer_assertion,
        brand_assertions=brand_assertions,
    )


def _unresolved(reference_type: str, reason: str) -> ReferenceResolutionResult:
    return ReferenceResolutionResult(
        input_value=None,
        resolved_value=None,
        status="unresolved",
        reference_type=reference_type,
        reason=reason,
    )


def _extract_verified_source_identity(
    extracted_text: str,
) -> tuple[str, Literal["manufacturer", "brand"]] | None:
    """Extract only explicitly labelled identity from already verified text."""
    patterns = (
        ("manufacturer", r"(?im)^\s*manufacturer(?:\s+name)?\s*[:=-]\s*(.+?)\s*$"),
        ("brand", r"(?im)^\s*brand(?:\s+name)?\s*[:=-]\s*(.+?)\s*$"),
    )
    for kind, pattern in patterns:
        match = re.search(pattern, extracted_text)
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip(" .,-")
            if value:
                return value, kind  # type: ignore[return-value]
    return None


def _trusted_identity_from_verified_sources(
    row: CatalogInputRow,
    identities: Sequence[tuple[ManufacturerSource, str, str]],
) -> tuple[str, Literal["manufacturer", "brand"], ManufacturerSource] | None:
    """Select one consistent page identity without using source metadata."""
    if not identities:
        return None
    distinct = {
        (normalize_reference_value(value), kind)
        for _source, value, kind in identities
    }
    if len(distinct) != 1:
        return None
    source, value, kind = identities[0]
    conflict = _catalogue_identity_conflict(row, value)
    if conflict is not None:
        return None
    return value, kind, source


def _map_resolved_identity(
    delivery_row: dict[str, str], resolution: CatalogReferenceResolution
) -> None:
    if resolution.manufacturer.status == "resolved" and "MANUFACTURER_NAME" in delivery_row:
        delivery_row["MANUFACTURER_NAME"] = str(resolution.manufacturer.resolved_value)
    for result in resolution.brands.values():
        if result.status == "resolved" and "BRAND_NAME" in delivery_row:
            delivery_row["BRAND_NAME"] = str(result.resolved_value)
            break
    if (
        resolution.runtime_identity is not None
        and resolution.runtime_identity.status == "resolved"
        and resolution.runtime_identity.reference_type == "brand"
        and "BRAND_NAME" in delivery_row
    ):
        delivery_row["BRAND_NAME"] = str(resolution.runtime_identity.resolved_value)


def _map_verified_source_metadata(
    delivery_row: dict[str, str],
    diagnostics: list[EnrichmentSourceDiagnostic],
    row: CatalogInputRow,
) -> None:
    successful = [diagnostic for diagnostic in diagnostics if diagnostic.success]
    if successful:
        delivery_row["MFR URL"] = successful[0].url
        delivery_row["MANUFACTURER_PART_NUMBER"] = row.Mfg_Part_Num


def _map_validated_attributes(
    delivery_row: dict[str, str],
    pipeline_result: ProductIntelligenceResult,
    mappings: AttributeDeliveryMappings,
    attribute_reference: AttributeReference | None,
    uom_reference: UOMReference | None,
) -> list[MappingDiagnostic]:
    """Place accepted attributes into generic delivery slots by canonical name.

    Validation and reference gates run before ordering. Explicit mapping
    profiles may supply canonical names, labels, and reference metadata, but
    their legacy ``slot`` values are deliberately ignored here.
    """
    diagnostics: list[MappingDiagnostic] = []
    category = pipeline_result.product_identification.product_category
    definitions = {
        definition.name: definition
        for definition in pipeline_result.product_identification.attributes
    }
    accepted: list[tuple[str, str, str, str, str]] = []

    for attribute in pipeline_result.validation.attributes:
        mapping = mappings.resolve(attribute.name, category=category)
        canonical_name = mapping.canonical_name if mapping is not None else attribute.name
        if attribute.status == "not_found":
            diagnostics.append(_skipped_for_attribute(attribute.name, None, "The attribute was not found in the sources.", "ATTRIBUTE_NOT_FOUND"))
            continue
        if attribute.status == "conflict":
            diagnostics.append(_skipped_for_attribute(attribute.name, None, "Conflicting source values require review.", "ATTRIBUTE_CONFLICT"))
            continue
        confidence = next(
            (item for item in pipeline_result.confidence.attributes if item.name == attribute.name),
            None,
        )
        minimum_confidence = mapping.minimum_confidence_level if mapping is not None else "medium"
        if confidence is not None and _confidence_below(confidence.level, minimum_confidence):
            diagnostics.append(_skipped_for_attribute(attribute.name, None, "Confidence is below the controlled mapping threshold; the attribute requires review.", "LOW_CONFIDENCE"))
            continue
        if not attribute.values:
            diagnostics.append(_skipped_for_attribute(attribute.name, None, "No validated source value was available.", "EVIDENCE_MISSING"))
            continue

        value = attribute.values[0].value
        definition = definitions.get(attribute.name)
        declared_uom = definition.unit if definition is not None else None
        delivery_uom_reference = mapping.uom_reference_name if mapping is not None else declared_uom
        delivery_value = _remove_expected_uom(value, delivery_uom_reference)
        reference_name = mapping.value_reference_name if mapping is not None else canonical_name
        if attribute_reference is not None:
            value_result = attribute_reference.validate_value(category, reference_name, delivery_value)
            if value_result.status != "resolved":
                code = "ATTRIBUTE_REFERENCE_MISSING" if value_result.status == "unresolved" else "ATTRIBUTE_VALUE_NOT_APPROVED"
                diagnostics.append(_skipped_for_attribute(attribute.name, None, value_result.reason, code))
                continue

        delivery_uom = ""
        if delivery_uom_reference:
            if uom_reference is not None:
                uom_result = uom_reference.resolve(delivery_uom_reference)
                if uom_result.status != "resolved":
                    diagnostics.append(_skipped_for_attribute(attribute.name, None, uom_result.reason, "UOM_NOT_APPROVED"))
                    continue
                allowed_uoms = (
                    attribute_reference.allowed_uoms(category, reference_name)
                    if attribute_reference is not None else ()
                )
                if allowed_uoms and normalize_reference_value(str(uom_result.resolved_value)) not in {
                    normalize_reference_value(item) for item in allowed_uoms
                }:
                    diagnostics.append(_skipped_for_attribute(attribute.name, None, "The UOM is not approved for this attribute.", "UOM_NOT_APPROVED"))
                    continue
                delivery_uom = str(uom_result.resolved_value)
            else:
                allowed_uoms = (
                    attribute_reference.allowed_uoms(category, reference_name)
                    if attribute_reference is not None else ()
                )
                if allowed_uoms and normalize_reference_value(delivery_uom_reference) not in {
                    normalize_reference_value(item) for item in allowed_uoms
                }:
                    diagnostics.append(_skipped_for_attribute(attribute.name, None, "The UOM is not approved for this attribute.", "UOM_NOT_APPROVED"))
                    continue
                # No UOM reference means there is no approved canonical UOM
                # to substitute. Preserve the declared source wording only
                # when no stricter allowed-UOM list rejects it.
                delivery_uom = delivery_uom_reference

        label = mapping.delivery_label if mapping is not None else _display_attribute_label(canonical_name)
        accepted.append((canonical_name, label, delivery_value, delivery_uom))

    # Canonical identity is application data, unlike the order in which
    # Gemini happened to return definitions.  Case-folding keeps this key
    # deterministic without changing the canonical spelling used for labels.
    accepted.sort(key=lambda item: (normalize_reference_value(item[0]), item[0]))
    for next_slot, (canonical_name, label, delivery_value, delivery_uom) in enumerate(
        accepted[:MAX_DELIVERY_ATTRIBUTE_SLOTS], start=1
    ):
        delivery_row[f"ATTRIBUTE_LABEL {next_slot}"] = label
        delivery_row[f"ATTRIBUTE_VALUE {next_slot}"] = delivery_value
        delivery_row[f"ATTRIBUTE_UOM {next_slot}"] = delivery_uom
        diagnostics.append(
            MappingDiagnostic(
                attribute_name=canonical_name,
                slot=next_slot,
                status="mapped",
                reason=(
                    f"Mapped sequentially to delivery slot {next_slot}; "
                    "the slot carries no fixed semantic meaning."
                ),
            )
        )
    for canonical_name, _label, _value, _uom in accepted[MAX_DELIVERY_ATTRIBUTE_SLOTS:]:
        diagnostics.append(_skipped_for_attribute(
            canonical_name,
            None,
            "ATTRIBUTE_SLOT_LIMIT_EXCEEDED: more than 50 validated attributes are available.",
            "ATTRIBUTE_SLOT_LIMIT_EXCEEDED",
        ))
    return diagnostics


def _remove_expected_uom(value: str, expected_uom: str | None) -> str:
    if not expected_uom:
        return value
    pattern = rf"\s+{re.escape(expected_uom.strip())}\s*$"
    stripped = re.sub(pattern, "", value, flags=re.IGNORECASE)
    return stripped if stripped else value


def _skipped(
    mapping: AttributeDeliveryMapping,
    reason: str,
    attribute_name: str | None = None,
) -> MappingDiagnostic:
    return MappingDiagnostic(
        attribute_name=attribute_name or mapping.canonical_name,
        slot=mapping.slot,
        status="skipped",
        reason=reason,
    )


def _skipped_for_attribute(
    attribute_name: str,
    slot: int | None,
    reason: str,
    code: str | None = None,
) -> MappingDiagnostic:
    return MappingDiagnostic(
        attribute_name=attribute_name,
        slot=slot,
        status="skipped",
        reason=reason,
        code=code,
    )


def _display_attribute_label(name: str) -> str:
    """Create a stable label from a canonical lower_snake_case identity."""
    return name.replace("_", " ").title()


def _confidence_below(actual: str, minimum: str) -> bool:
    levels = {"low": 0, "medium": 1, "high": 2}
    return levels.get(actual, 0) < levels.get(minimum, 1)


def _finish_result(
    row: CatalogInputRow,
    delivery_row: dict[str, str],
    source_diagnostics: list[EnrichmentSourceDiagnostic],
    reference_resolution: CatalogReferenceResolution,
    mapping_diagnostics: list[MappingDiagnostic],
    pipeline_result: ProductIntelligenceResult | None,
    expected_delivery_row: dict[str, str] | None,
    schema: DeliverySchema,
    delivery_evidence: dict[str, DeliveryFieldEvidence] | None = None,
) -> CatalogueEnrichmentResult:
    evaluation = None
    if expected_delivery_row is not None:
        comparison = compare_delivery_rows(delivery_row, expected_delivery_row, schema)
        evaluation = EvaluationComparison(
            mfg_part_num=comparison.mfg_part_num,
            matches=comparison.matches,
            differences=[
                EvaluationFieldDifference(
                    column=difference.field,
                    generated_value=difference.generated,
                    expected_value=difference.expected,
                )
                for difference in comparison.differences
            ],
        )
    result = CatalogueEnrichmentResult(
        catalogue_row=row,
        pipeline_result=pipeline_result,
        delivery_row=schema.validate_row(delivery_row),
        delivery_evidence=delivery_evidence or {},
        source_diagnostics=source_diagnostics,
        reference_resolution=reference_resolution,
        mapping_diagnostics=mapping_diagnostics,
        evaluation_comparison=evaluation,
    )
    result.review = build_review_report(
        pipeline_result=pipeline_result,
        source_diagnostics=source_diagnostics,
        reference_resolution=reference_resolution,
        mapping_diagnostics=mapping_diagnostics,
        evaluation_comparison=evaluation,
    )
    return result


def _pipeline_error_message(error: Exception) -> str:
    """Retain useful nested validation context without changing firewall behavior."""
    messages: list[str] = []
    current: BaseException | None = error
    while current is not None and len(messages) < 6:
        message = str(current).strip()
        if message and message not in messages:
            messages.append(message)
        current = current.__cause__ or current.__context__
    return " | ".join(messages) or "Unexpected pipeline failure."
