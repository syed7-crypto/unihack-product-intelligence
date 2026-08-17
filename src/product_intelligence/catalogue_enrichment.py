"""Generic catalogue-row enrichment orchestration.

This module connects the existing catalogue, manufacturer-source, product
intelligence, reference, and delivery layers. It does not discover sources or
invent missing reference data.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .catalog_input import CatalogInputRow, brand_candidate
from .controlled_attribute_mapping import (
    ControlledAttributeMapping,
    ControlledAttributeMappingRegistry,
)
from .delivery_output import compare_delivery_rows, map_raw_fields_to_delivery
from .delivery_schema import DeliverySchema
from .manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    ManufacturerSource,
    RetrievalResult,
)
from .pipeline import ProductIntelligenceResult, run_pipeline
from .reference_data import (
    AttributeReference,
    BrandReference,
    CatalogReferenceResolution,
    ManufacturerReference,
    ReferenceResolutionResult,
    UOMReference,
    normalize_reference_value,
)
from .review import ReviewReport, build_review_report


AttributeDeliveryMapping = ControlledAttributeMapping
AttributeDeliveryMappings = ControlledAttributeMappingRegistry


class EnrichmentSourceDiagnostic(BaseModel):
    """Outcome of retrieving and normalizing one explicit source URL."""

    url: str
    success: bool
    source_type: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    exact_mpn_verified: bool = False
    error: str | None = None


class MappingDiagnostic(BaseModel):
    """Reason an attribute mapping was populated or safely left blank."""

    attribute_name: str
    slot: int | None = None
    status: Literal["mapped", "skipped"]
    reason: str


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
    source_diagnostics: list[EnrichmentSourceDiagnostic] = Field(default_factory=list)
    reference_resolution: CatalogReferenceResolution | None = None
    mapping_diagnostics: list[MappingDiagnostic] = Field(default_factory=list)
    evaluation_comparison: EvaluationComparison | None = None
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
    attribute_reference: AttributeReference | None = None,
    uom_reference: UOMReference | None = None,
    attribute_mappings: AttributeDeliveryMappings | None = None,
    expected_delivery_row: dict[str, str] | None = None,
    verified_sources: Sequence[ManufacturerSource] | None = None,
    initial_source_diagnostics: Sequence[EnrichmentSourceDiagnostic] = (),
) -> CatalogueEnrichmentResult:
    """Enrich one row using only explicit, exact-MPN-verified sources.

    Retrieval failures are retained in diagnostics. If every source fails,
    the result contains only the preserved raw catalogue fields and no
    pipeline result or enrichment values.
    """
    if not source_urls and verified_sources is None:
        raise CatalogueEnrichmentError("At least one explicit source URL is required.")

    provider = provider or ManufacturerEnrichmentProvider()
    mappings = attribute_mappings or AttributeDeliveryMappings()
    delivery_row = map_raw_fields_to_delivery(catalogue_row, delivery_schema)
    reference_resolution = _resolve_references(
        catalogue_row, manufacturer_reference, brand_reference
    )
    _map_resolved_identity(delivery_row, reference_resolution)

    normalized_sources = []
    diagnostics: list[EnrichmentSourceDiagnostic] = list(initial_source_diagnostics)

    for source in verified_sources or ():
        if not source.exact_mpn_verified:
            diagnostics.append(
                EnrichmentSourceDiagnostic(
                    url=source.url,
                    success=False,
                    source_type=source.source_type,
                    source_name=source.source_name,
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
                    error=f"Source normalization failed: {error}",
                )
            )
            continue
        normalized_sources.append(normalized)
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
        retrieval = provider.retrieve_source(url, catalogue_row.Mfg_Part_Num)
        if not retrieval.success or retrieval.source is None:
            diagnostics.append(
                EnrichmentSourceDiagnostic(
                    url=url,
                    success=False,
                    error=retrieval.error or "Source retrieval failed.",
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
                    error=f"Source normalization failed: {error}",
                )
            )
            continue
        normalized_sources.append(normalized)
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
        )

    try:
        pipeline_result = run_pipeline(normalized_sources, client=client)
    except Exception as error:
        diagnostics.append(
            EnrichmentSourceDiagnostic(
                url=";".join(source_urls),
                success=False,
                error=f"Pipeline failed after source verification: {_pipeline_error_message(error)}",
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
        )

    _map_verified_source_metadata(delivery_row, diagnostics, catalogue_row)
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
    )


def _resolve_references(
    row: CatalogInputRow,
    manufacturer_reference: ManufacturerReference | None,
    brand_reference: BrandReference | None,
) -> CatalogReferenceResolution:
    if manufacturer_reference is None:
        manufacturer = _unresolved("manufacturer", "No manufacturer reference was configured.")
    else:
        manufacturer = manufacturer_reference.resolve(row.Part_Manuf)

    brands: dict[str, ReferenceResolutionResult] = {}
    for field in ("E1_Brand", "Unilog_Brand", "DIB_Brand"):
        candidate = brand_candidate(getattr(row, field))
        if brand_reference is None:
            result = _unresolved("brand", "No brand reference was configured.")
        else:
            result = brand_reference.resolve(candidate)
        if candidate is None:
            result = result.model_copy(update={"input_value": getattr(row, field)})
        brands[field] = result
    return CatalogReferenceResolution(manufacturer=manufacturer, brands=brands)


def _unresolved(reference_type: str, reason: str) -> ReferenceResolutionResult:
    return ReferenceResolutionResult(
        input_value=None,
        resolved_value=None,
        status="unresolved",
        reference_type=reference_type,
        reason=reason,
    )


def _map_resolved_identity(
    delivery_row: dict[str, str], resolution: CatalogReferenceResolution
) -> None:
    if resolution.manufacturer.status == "resolved":
        delivery_row["MANUFACTURER_NAME"] = str(resolution.manufacturer.resolved_value)
    for result in resolution.brands.values():
        if result.status == "resolved":
            delivery_row["BRAND_NAME"] = str(result.resolved_value)
            break


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
    diagnostics: list[MappingDiagnostic] = []
    category = pipeline_result.product_identification.product_category
    validated = {attribute.name: attribute for attribute in pipeline_result.validation.attributes}

    for mapping in mappings.mappings:
        attribute = next(
            (
                candidate
                for candidate in pipeline_result.validation.attributes
                if mapping.matches(candidate.name, category)
            ),
            None,
        )
        diagnostic_name = attribute.name if attribute is not None else mapping.canonical_name
        if attribute is None:
            diagnostics.append(
                _skipped(mapping, "The extracted schema did not contain this attribute.", diagnostic_name)
            )
            continue
        if attribute.status == "not_found":
            diagnostics.append(_skipped(mapping, "The attribute was not found in the sources.", diagnostic_name))
            continue
        if attribute.status == "conflict":
            diagnostics.append(_skipped(mapping, "Conflicting source values require review.", diagnostic_name))
            continue
        confidence = next(
            (item for item in pipeline_result.confidence.attributes if item.name == attribute.name),
            None,
        )
        if confidence is not None and _confidence_below(confidence.level, mapping.minimum_confidence_level):
            diagnostics.append(_skipped(mapping, "Confidence is below the controlled mapping threshold; the attribute requires review.", diagnostic_name))
            continue
        if not attribute.values:
            diagnostics.append(_skipped(mapping, "No validated source value was available.", diagnostic_name))
            continue

        value = attribute.values[0].value
        delivery_uom_reference = mapping.uom_reference_name
        delivery_value = _remove_expected_uom(value, delivery_uom_reference)
        reference_name = mapping.value_reference_name
        if attribute_reference is None:
            diagnostics.append(_skipped(mapping, "No controlled attribute reference was configured.", diagnostic_name))
            continue
        value_result = attribute_reference.validate_value(category, reference_name, delivery_value)
        if value_result.status != "resolved":
            diagnostics.append(_skipped(mapping, value_result.reason, diagnostic_name))
            continue

        delivery_uom = ""
        if delivery_uom_reference:
            if uom_reference is None:
                diagnostics.append(_skipped(mapping, "No controlled UOM reference was configured.", diagnostic_name))
                continue
            uom_result = uom_reference.resolve(delivery_uom_reference)
            if uom_result.status != "resolved":
                diagnostics.append(_skipped(mapping, uom_result.reason, diagnostic_name))
                continue
            allowed_uoms = attribute_reference.allowed_uoms(category, reference_name)
            if allowed_uoms and normalize_reference_value(str(uom_result.resolved_value)) not in {
                normalize_reference_value(item) for item in allowed_uoms
            }:
                diagnostics.append(_skipped(mapping, "The UOM is not approved for this attribute.", diagnostic_name))
                continue
            delivery_uom = str(uom_result.resolved_value)

        delivery_row[f"ATTRIBUTE_LABEL {mapping.slot}"] = mapping.delivery_label
        delivery_row[f"ATTRIBUTE_VALUE {mapping.slot}"] = delivery_value
        delivery_row[f"ATTRIBUTE_UOM {mapping.slot}"] = delivery_uom
        diagnostics.append(
            MappingDiagnostic(
                attribute_name=attribute.name,
                slot=mapping.slot,
                status="mapped",
                reason=f"Mapped using {mapping.mapping_source} controlled mapping and validated evidence.",
            )
        )
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
