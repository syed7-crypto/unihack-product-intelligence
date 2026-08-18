"""Safe deterministic normalization for exact manufacturer part matching."""

from __future__ import annotations

import re
import unicodedata


_SEPARATOR_TRANSLATION = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    "_": "-",
})


def normalize_mpn(value: str) -> str:
    """Normalize Unicode, case, and controlled separators only.

    Characters are never removed and no fuzzy/edit-distance matching is
    performed. Whitespace and common dash variants are represented as a
    single ASCII hyphen.
    """
    normalized = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    normalized = normalized.translate(_SEPARATOR_TRANSLATION)
    normalized = re.sub(r"\s+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized)
    return normalized

