"""Bounded, order-preserving execution for independent search requests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any


@dataclass(frozen=True)
class SearchCallResult:
    results: list[Any]
    error: Exception | None = None


def search_in_order(
    provider: Any,
    queries: Sequence[str],
    max_results: int,
    *,
    concurrency: int = 1,
) -> list[SearchCallResult]:
    """Execute independent searches with bounded workers and input ordering.

    Futures may finish in any order, but the returned list always follows the
    original query order. A failed request is represented in its own result so
    successful requests are retained.
    """
    if concurrency < 1:
        raise ValueError("search concurrency must be positive.")
    if concurrency == 1 or len(queries) <= 1:
        return [_one_search(provider, query, max_results) for query in queries]
    worker_count = min(concurrency, len(queries))
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="unihack-search",
    ) as executor:
        futures = [
            executor.submit(_one_search, provider, query, max_results)
            for query in queries
        ]
        return [future.result() for future in futures]


def _one_search(
    provider: Any,
    query: str,
    max_results: int,
) -> SearchCallResult:
    try:
        return SearchCallResult(provider.search(query, max_results))
    except Exception as error:
        return SearchCallResult([], error)
