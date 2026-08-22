"""Small, non-invasive timing accumulator for one catalogue batch."""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from time import perf_counter
from collections.abc import Iterator

from pydantic import BaseModel, Field


class SearchTimingRecord(BaseModel):
    """One bounded search-call diagnostic without provider payloads."""

    mpn: str
    query: str
    query_kind: str
    duration_seconds: float = Field(ge=0)
    result_count: int = Field(ge=0)
    error_category: str | None = None


class RuntimeTimingSummary(BaseModel):
    """Aggregate phase timings and call counts; no provider payloads are kept."""

    total_batch_duration_seconds: float = Field(default=0.0, ge=0)
    serper_search_duration_seconds: float = Field(default=0.0, ge=0)
    serper_search_calls: int = Field(default=0, ge=0)
    domain_search_duration_seconds: float = Field(default=0.0, ge=0)
    domain_search_calls: int = Field(default=0, ge=0)
    source_retrieval_duration_seconds: float = Field(default=0.0, ge=0)
    source_retrieval_calls: int = Field(default=0, ge=0)
    product_identification_duration_seconds: float = Field(default=0.0, ge=0)
    product_identification_calls: int = Field(default=0, ge=0)
    attribute_extraction_duration_seconds: float = Field(default=0.0, ge=0)
    attribute_extraction_calls: int = Field(default=0, ge=0)
    validation_delivery_mapping_duration_seconds: float = Field(default=0.0, ge=0)


class RuntimeTimingAccumulator:
    """Thread-safe aggregate for a single batch; deliberately not a tracer."""

    def __init__(self, *, clock=perf_counter) -> None:
        self._clock = clock
        self._lock = Lock()
        self._search_records: list[SearchTimingRecord] = []
        self._values = {
            field: 0.0
            for field in RuntimeTimingSummary.model_fields
        }

    def add_duration(self, field: str, duration: float) -> None:
        with self._lock:
            self._values[field] += max(0.0, duration)

    def increment(self, field: str, amount: int = 1) -> None:
        with self._lock:
            self._values[field] += amount

    def set_total(self, duration: float) -> None:
        with self._lock:
            self._values["total_batch_duration_seconds"] = max(0.0, duration)

    def record_search(
        self,
        *,
        mpn: str,
        query: str,
        query_kind: str,
        duration_seconds: float,
        result_count: int,
        error_category: str | None = None,
    ) -> None:
        record = SearchTimingRecord(
            mpn=mpn,
            query=query,
            query_kind=query_kind,
            duration_seconds=max(0.0, duration_seconds),
            result_count=max(0, result_count),
            error_category=error_category,
        )
        with self._lock:
            self._search_records.append(record)
            self._values["serper_search_calls"] += 1
            self._values["serper_search_duration_seconds"] += record.duration_seconds
            if query_kind == "domain_constrained":
                self._values["domain_search_calls"] += 1
                self._values["domain_search_duration_seconds"] += record.duration_seconds

    def search_snapshot(self) -> list[SearchTimingRecord]:
        with self._lock:
            return list(self._search_records)

    def now(self) -> float:
        """Return the monotonic clock value used by this accumulator."""
        return self._clock()

    @contextmanager
    def measure(self, duration_field: str, call_field: str | None = None) -> Iterator[None]:
        if call_field is not None:
            self.increment(call_field)
        started = self._clock()
        try:
            yield
        finally:
            self.add_duration(duration_field, self._clock() - started)

    def snapshot(self) -> RuntimeTimingSummary:
        with self._lock:
            return RuntimeTimingSummary(**self._values)
