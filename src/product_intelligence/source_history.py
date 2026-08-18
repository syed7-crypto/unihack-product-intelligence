"""Reusable, non-authoritative history of previously verified source URLs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .manufacturer_enrichment import ManufacturerSource
from .reference_data import normalize_reference_value
from .source_discovery import ManufacturerSourcePolicy


class VerifiedSourceRecord(BaseModel):
    """A historical verification fact, never a current verification result."""

    manufacturer_identity: str = Field(min_length=1)
    mfg_part_num: str = Field(min_length=1)
    verified_url: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    verification_result: Literal["verified"] = "verified"
    source_type: str
    verified_at: str
    metadata: dict[str, str] = Field(default_factory=dict)


class VerifiedSourceHistory:
    """Deterministic history store; it only proposes URLs."""

    def __init__(self, records: Sequence[VerifiedSourceRecord] = ()) -> None:
        self._records: list[VerifiedSourceRecord] = []
        for record in records:
            self._validate_record(record)
            self._records.append(record)

    def record_verified_source(
        self,
        manufacturer_identity: str,
        mfg_part_num: str,
        source: ManufacturerSource,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> VerifiedSourceRecord:
        """Record a source only after current exact-MPN verification."""
        if not source.exact_mpn_verified:
            raise ValueError("Only exact-MPN-verified sources can enter history.")
        parsed = urlparse(source.url)
        domain = (parsed.hostname or "").casefold().rstrip(".")
        record = VerifiedSourceRecord(
            manufacturer_identity=manufacturer_identity,
            mfg_part_num=mfg_part_num,
            verified_url=source.url,
            domain=domain,
            source_type=source.source_type,
            verified_at=datetime.now(timezone.utc).isoformat(),
            metadata=dict(metadata or {}),
        )
        self._validate_record(record)
        if not any(
            existing.manufacturer_identity == record.manufacturer_identity
            and existing.mfg_part_num == record.mfg_part_num
            and existing.verified_url == record.verified_url
            for existing in self._records
        ):
            self._records.append(record)
        return record

    def candidate_urls(
        self, manufacturer_identity: str, policy: ManufacturerSourcePolicy
    ) -> list[str]:
        """Return historical URLs allowed by the current policy.

        The historical MPN does not verify the current product; callers must
        send every returned URL through normal retrieval and exact matching.
        """
        identity = normalize_reference_value(manufacturer_identity)
        urls: list[str] = []
        for record in self._records:
            if normalize_reference_value(record.manufacturer_identity) != identity:
                continue
            if not policy.domain_allowed(record.domain):
                continue
            if record.verified_url not in urls:
                urls.append(record.verified_url)
        return urls

    def records(self) -> tuple[VerifiedSourceRecord, ...]:
        return tuple(self._records)

    @staticmethod
    def _validate_record(record: VerifiedSourceRecord) -> None:
        parsed = urlparse(record.verified_url)
        domain = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme.casefold() != "https" or not domain:
            raise ValueError("Verified source history requires an HTTPS URL.")
        if domain != record.domain.casefold().rstrip("."):
            raise ValueError("Verified source history domain does not match its URL.")

