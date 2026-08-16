"""Small, manually verified source policies for the six-row pilot.

PILOT-ONLY CONFIGURATION. These domains were manually verified for this
hackathon pilot and are not official UniHack reference data. This registry
contains governance metadata only; it contains no product attributes,
delivery values, or retailer domains.
"""

from __future__ import annotations

from .catalog_input import CatalogInputRow
from .source_discovery import ManufacturerSourcePolicy


_FRIGIDAIRE = ManufacturerSourcePolicy(
    manufacturer_name="Frigidaire",
    approved_domains=("www.frigidaire.com", "frigidaire.com", "frigidaire.bynder.com"),
)
_HUNTER = ManufacturerSourcePolicy(
    manufacturer_name="Hunter",
    approved_domains=("www.hunterfan.com", "hunterfan.com", "image.hunterfan.com"),
)
_LEVITON = ManufacturerSourcePolicy(
    manufacturer_name="Leviton",
    approved_domains=("leviton.com", "content.leviton.com"),
)


_PILOT_POLICIES: dict[str, ManufacturerSourcePolicy] = {
    "PDSH4816AF": _FRIGIDAIRE,
    "59210": _HUNTER,
    "S03-05226-IS": _LEVITON,
}


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
    policy = _PILOT_POLICIES.get(mfg_part_num)
    return policy.model_copy(deep=True) if policy is not None else None
