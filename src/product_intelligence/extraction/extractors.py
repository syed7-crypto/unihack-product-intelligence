"""Extract TXT, JSON, and PDF files into a common source representation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader


class ExtractionError(ValueError):
    """Raised when a supported source cannot be read or parsed."""


@dataclass(frozen=True)
class SourceLocation:
    """A location within a source, such as a PDF page."""

    label: str
    page_number: int | None = None


@dataclass(frozen=True)
class NormalizedSource:
    """Common representation consumed by later pipeline stages."""

    source_id: str
    source_type: str
    source_name: str
    extracted_text: str
    locations: tuple[SourceLocation, ...]


def extract_file(file_path: str | Path) -> NormalizedSource:
    """Extract a supported file based on its extension."""
    path = _validate_path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".txt":
        return extract_txt(path)
    if suffix == ".json":
        return extract_json(path)
    if suffix == ".pdf":
        return extract_pdf(path)

    raise ExtractionError(
        f"Unsupported file type '{path.suffix or '[no extension]'}'. "
        "Supported types are TXT, JSON, and PDF."
    )


def extract_txt(file_path: str | Path) -> NormalizedSource:
    """Read a UTF-8 text file."""
    path = _validate_path(file_path)
    raw = _read_bytes(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ExtractionError(f"Could not decode TXT file '{path.name}' as UTF-8.") from error

    text = _require_text(text, path)
    return _source(path, "txt", text, (SourceLocation("document"),))


def extract_json(file_path: str | Path) -> NormalizedSource:
    """Parse JSON and serialize it as readable text for downstream processing."""
    path = _validate_path(file_path)
    raw = _read_bytes(path)
    try:
        data: Any = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise ExtractionError(f"Could not decode JSON file '{path.name}' as UTF-8.") from error
    except json.JSONDecodeError as error:
        raise ExtractionError(f"Could not parse JSON file '{path.name}': {error.msg}.") from error

    text = json.dumps(data, indent=2, ensure_ascii=False)
    return _source(path, "json", text, (SourceLocation("document"),))


def extract_pdf(file_path: str | Path) -> NormalizedSource:
    """Extract text from each PDF page while preserving page locations."""
    path = _validate_path(file_path)
    _read_bytes(path)
    try:
        reader = PdfReader(str(path), strict=False)
        page_text: list[str] = []
        locations: list[SourceLocation] = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                page_text.append(text)
            locations.append(SourceLocation(f"page {page_number}", page_number))
    except Exception as error:
        raise ExtractionError(f"Could not read PDF file '{path.name}': {error}") from error

    extracted_text = "\n\n".join(page_text)
    if not extracted_text:
        raise ExtractionError(f"PDF file '{path.name}' did not contain extractable text.")
    return _source(path, "pdf", extracted_text, tuple(locations))


def _validate_path(file_path: str | Path) -> Path:
    path = Path(file_path)
    if not path.exists():
        raise ExtractionError(f"Source file does not exist: '{path}'.")
    if not path.is_file():
        raise ExtractionError(f"Source path is not a file: '{path}'.")
    return path


def _read_bytes(path: Path) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ExtractionError(f"Could not read source file '{path.name}': {error}") from error
    if not raw:
        raise ExtractionError(f"Source file '{path.name}' is empty.")
    return raw


def _require_text(text: str, path: Path) -> str:
    if not text.strip():
        raise ExtractionError(f"Source file '{path.name}' does not contain any text.")
    return text


def _source(
    path: Path,
    source_type: str,
    extracted_text: str,
    locations: tuple[SourceLocation, ...],
) -> NormalizedSource:
    source_id = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return NormalizedSource(
        source_id=source_id,
        source_type=source_type,
        source_name=path.name,
        extracted_text=extracted_text,
        locations=locations,
    )
