"""Structured diagnostics shared by pipeline stages."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Diagnostic(BaseModel):
    """Stable machine-readable code with a human-readable explanation."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    source_id: str | None = None
    source_name: str | None = None

