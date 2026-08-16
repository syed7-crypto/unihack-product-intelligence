"""Controlled manufacturer/brand source-policy governance.

The registry is deliberately small and contains only manually verified pilot
identities and domains. It contains governance metadata only; it contains no
product attributes, delivery values, or retailer domains.

The six MPN entries at the bottom are compatibility fixtures for the original
pilot rows. New products are resolved by controlled manufacturer/brand identity
through ``resolve_source_policy_for_row``; they do not require an MPN policy.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .catalog_input import CatalogInputRow
from .reference_data import BrandReference, ManufacturerReference, normalize_reference_value
from .source_discovery import ManufacturerSourcePolicy, SourceKind


PolicyIdentityKind = Literal["manufacturer", "brand"]


class ControlledSourcePolicy(BaseModel):
    """Governance record for one approved manufacturer or brand identity."""

    controlled_identity: str = Field(min_length=1)
    identity_kind: PolicyIdentityKind
    approved_domains: tuple[str, ...] = ()
    governance_reason: str = Field(min_length=1)
    allowed_source_kinds: tuple[SourceKind, ...] = ("webpage", "pdf")
    query_templates: tuple[str, ...] = (
        "{part_number}",
        "{part_number} {manufacturer}",
        "{part_number} {brand}",
    )

    @model_validator(mode="after")
    def validate_policy(self) -> "ControlledSourcePolicy":
        if not self.approved_domains:
            raise ValueError("A controlled source policy requires approved domains.")
        normalized = tuple(
            normalize_reference_value(domain).rstrip(".")
            for domain in self.approved_domains
            if domain.strip()
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError("Approved manufacturer domains must be unique.")
        self.approved_domains = normalized
        if not self.query_templates:
            raise ValueError("A controlled source policy requires query templates.")
        return self

    def as_discovery_policy(self) -> ManufacturerSourcePolicy:
        """Convert governance metadata to the existing discovery contract."""
        return ManufacturerSourcePolicy(
            manufacturer_name=self.controlled_identity,
            approved_domains=self.approved_domains,
            allowed_source_kinds=self.allowed_source_kinds,
            query_templates=self.query_templates,
        )


def _controlled(
    identity: str,
    kind: PolicyIdentityKind,
    domains: tuple[str, ...],
) -> ControlledSourcePolicy:
    return ControlledSourcePolicy(
        controlled_identity=identity,
        identity_kind=kind,
        approved_domains=domains,
        governance_reason=(
            "Manually verified pilot manufacturer/brand identity and official domains; "
            "governance metadata only."
        ),
    )


_FRIGIDAIRE = _controlled(
    "Frigidaire", "manufacturer",
    ("www.frigidaire.com", "frigidaire.com", "frigidaire.bynder.com"),
)
_HUNTER = _controlled(
    "Hunter", "manufacturer",
    ("www.hunterfan.com", "hunterfan.com", "image.hunterfan.com"),
)
_LEVITON = _controlled(
    "Leviton", "manufacturer",
    ("leviton.com", "content.leviton.com"),
)
_KITCHENAID = _controlled(
    "KitchenAid", "brand",
    ("kitchenaid.com", "www.kitchenaid.com"),
)
_DEWALT = _controlled(
    "DEWALT", "brand",
    ("dewalt.com", "www.dewalt.com"),
)
_TREX = _controlled(
    "Trex", "brand",
    ("trex.com", "www.trex.com"),
)


_MANUFACTURER_POLICIES: dict[str, ControlledSourcePolicy] = {
    normalize_reference_value(policy.controlled_identity): policy
    for policy in (_FRIGIDAIRE, _HUNTER, _LEVITON)
}
_BRAND_POLICIES: dict[str, ControlledSourcePolicy] = {
    normalize_reference_value(policy.controlled_identity): policy
    for policy in (_KITCHENAID, _DEWALT, _TREX)
}


_LEGACY_PILOT_POLICIES: dict[str, ControlledSourcePolicy] = {
    "PDSH4816AF": _FRIGIDAIRE,
    "59210": _HUNTER,
    "S03-05226-IS": _LEVITON,
    "KDFM404KPS": _KITCHENAID,
    "DWST41092": _DEWALT,
    "543302126": _TREX,
}


def resolve_source_policy(
    *,
    manufacturer_identity: str | None = None,
    brand_identity: str | None = None,
) -> ManufacturerSourcePolicy | None:
    """Resolve a policy by controlled identity, manufacturer before brand."""
    if manufacturer_identity and manufacturer_identity.strip():
        policy = _MANUFACTURER_POLICIES.get(normalize_reference_value(manufacturer_identity))
        if policy is not None:
            return policy.as_discovery_policy()
    if brand_identity and brand_identity.strip():
        policy = _BRAND_POLICIES.get(normalize_reference_value(brand_identity))
        if policy is not None:
            return policy.as_discovery_policy()
    return None


def get_controlled_source_policy(
    identity: str,
    identity_kind: PolicyIdentityKind,
) -> ControlledSourcePolicy | None:
    """Return governance metadata for a controlled identity, if registered."""
    registry = _MANUFACTURER_POLICIES if identity_kind == "manufacturer" else _BRAND_POLICIES
    policy = registry.get(normalize_reference_value(identity))
    return policy.model_copy(deep=True) if policy is not None else None


def resolve_source_policy_for_row(
    row: CatalogInputRow,
    *,
    manufacturer_reference: ManufacturerReference | None = None,
    brand_reference: BrandReference | None = None,
) -> ManufacturerSourcePolicy | None:
    """Resolve a row using only controlled reference matches.

    Raw catalogue manufacturer/brand text is never trusted directly. The
    legacy six-row fixtures are checked last solely to preserve the original
    pilot behavior while new products use identity-based resolution.
    """
    if manufacturer_reference is not None:
        result = manufacturer_reference.resolve(row.Part_Manuf)
        if result.status == "resolved" and isinstance(result.resolved_value, str):
            policy = resolve_source_policy(manufacturer_identity=result.resolved_value)
            if policy is not None:
                return policy

    if brand_reference is not None:
        for candidate in row.brand_candidates().values():
            if candidate is None:
                continue
            result = brand_reference.resolve(candidate)
            if result.status == "resolved" and isinstance(result.resolved_value, str):
                policy = resolve_source_policy(brand_identity=result.resolved_value)
                if policy is not None:
                    return policy

    legacy = _LEGACY_PILOT_POLICIES.get(row.Mfg_Part_Num)
    return legacy.as_discovery_policy() if legacy is not None else None


def get_pilot_source_policy(
    row_or_mfg_part_num: CatalogInputRow | str,
) -> ManufacturerSourcePolicy | None:
    """Return a copy of the explicit pilot policy for a selected product.

    The MPN is used only to select a known pilot manufacturer policy. It does
    not imply that catalogue manufacturer/brand reference resolution succeeded
    and does not perform enrichment or identifier mapping.
    """
    mfg_part_num = (
        row_or_mfg_part_num.Mfg_Part_Num
        if isinstance(row_or_mfg_part_num, CatalogInputRow)
        else row_or_mfg_part_num
    )
    policy = _LEGACY_PILOT_POLICIES.get(mfg_part_num)
    return policy.as_discovery_policy() if policy is not None else None
