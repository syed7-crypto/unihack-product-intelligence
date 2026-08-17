"""Task 42 candidate coverage for observed, validated attribute names.

These mappings are deliberately candidate/mock configuration.  The
repository does not contain the official UniHack attribute, LOV, UOM, or
delivery-slot masters, so this module is not used as a production default.
Callers may supply the mappings together with controlled references for local
tests or a later governed deployment.
"""

from __future__ import annotations

from .controlled_attribute_mapping import (
    ControlledAttributeMapping,
    ControlledAttributeMappingRegistry,
)


_CANDIDATE_REASON = (
    "Candidate derived from an observed evidence-backed attribute name; "
    "official UniHack mapping and reference data are not present."
)


def task42_candidate_mappings() -> ControlledAttributeMappingRegistry:
    """Return reusable candidate mappings without activating them globally.

    Slots are explicit and unique.  They are configuration choices for a
    caller-provided candidate profile, not claims about official UniHack
    slot semantics.
    """
    return ControlledAttributeMappingRegistry(
        mappings=[
            ControlledAttributeMapping(
                internal_attribute_name="wheel_diameter",
                canonical_attribute_name="wheel_diameter",
                delivery_label="Wheel Diameter",
                aliases=("wheel diameter",),
                data_type="number",
                expected_uom="in",
                slot=16,
                mapping_source="mock",
                governance_status="candidate",
                governance_reason=_CANDIDATE_REASON,
            ),
            ControlledAttributeMapping(
                internal_attribute_name="wheel_thickness",
                canonical_attribute_name="wheel_thickness",
                delivery_label="Wheel Thickness",
                aliases=("thickness", "wheel thickness"),
                data_type="number",
                expected_uom="in",
                slot=17,
                mapping_source="mock",
                governance_status="candidate",
                governance_reason=_CANDIDATE_REASON,
            ),
            ControlledAttributeMapping(
                internal_attribute_name="arbor_size",
                canonical_attribute_name="arbor_size",
                delivery_label="Arbor Size",
                aliases=("arbor size",),
                data_type="number",
                expected_uom="in",
                slot=18,
                mapping_source="mock",
                governance_status="candidate",
                governance_reason=_CANDIDATE_REASON,
            ),
            ControlledAttributeMapping(
                internal_attribute_name="blade_span",
                canonical_attribute_name="blade_span",
                delivery_label="Blade Span",
                aliases=("blade span", "fan diameter"),
                data_type="number",
                expected_uom="in",
                slot=19,
                mapping_source="mock",
                governance_status="candidate",
                governance_reason=_CANDIDATE_REASON,
            ),
            ControlledAttributeMapping(
                internal_attribute_name="number_of_blades",
                canonical_attribute_name="number_of_blades",
                delivery_label="Number of Blades",
                aliases=("blade count", "number of blades"),
                data_type="number",
                slot=20,
                mapping_source="mock",
                governance_status="candidate",
                governance_reason=_CANDIDATE_REASON,
            ),
            ControlledAttributeMapping(
                internal_attribute_name="package_quantity",
                canonical_attribute_name="package_quantity",
                delivery_label="Package Quantity",
                aliases=("pack quantity", "package quantity"),
                data_type="number",
                slot=21,
                mapping_source="mock",
                governance_status="candidate",
                governance_reason=_CANDIDATE_REASON,
            ),
        ]
    )
