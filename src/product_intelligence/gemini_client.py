"""Small reusable client for calling the Gemini API."""

from __future__ import annotations

import os
import time
from pathlib import Path
from collections.abc import Callable
from typing import TypeVar

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel


DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
TRANSIENT_RETRY_DELAYS_SECONDS = (1.0, 4.0, 8.0)
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class GeminiTransientError(RuntimeError):
    """A transient Gemini service failure after bounded retries."""

    def __init__(self, message: str, *, attempts: int) -> None:
        self.attempts = attempts
        super().__init__(message)


class GeminiClient:
    """Configured Gemini text-generation client."""

    def __init__(
        self,
        model: str | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        load_dotenv(project_root / ".env")

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured. Add it to the local .env file."
            )

        self.model = model or os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
        self._client = genai.Client(api_key=api_key)
        self._sleep = sleep

    def generate_text(self, prompt: str) -> str:
        """Send a text prompt and return the generated response text."""
        if not prompt.strip():
            raise ValueError("The Gemini prompt must not be empty.")

        response = self._generate_with_retry(
            lambda: self._client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
        )
        response_text = response.text
        if not response_text:
            raise RuntimeError("Gemini returned an empty response.")
        return response_text

    def generate_structured_json(
        self,
        prompt: str,
        response_schema: type[ResponseModel],
    ) -> str:
        """Send a prompt and request JSON matching a Pydantic response schema."""
        if not prompt.strip():
            raise ValueError("The Gemini prompt must not be empty.")

        response = self._generate_with_retry(
            lambda: self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=response_schema,
                ),
            )
        )
        response_text = response.text
        if not response_text:
            raise RuntimeError("Gemini returned an empty response.")
        return response_text

    def _generate_with_retry(self, operation: Callable[[], object]) -> object:
        """Run one request, retrying only transient HTTP 503 failures."""
        for retry_index, delay in enumerate((0.0, *TRANSIENT_RETRY_DELAYS_SECONDS)):
            try:
                return operation()
            except Exception as error:
                if not _is_transient_503(error):
                    raise
                if retry_index == len(TRANSIENT_RETRY_DELAYS_SECONDS):
                    attempts = len(TRANSIENT_RETRY_DELAYS_SECONDS) + 1
                    raise GeminiTransientError(
                        f"Gemini transient failure after {attempts} attempts: {error}",
                        attempts=attempts,
                    ) from error
                self._sleep(TRANSIENT_RETRY_DELAYS_SECONDS[retry_index])
        raise AssertionError("Gemini retry loop did not return or raise.")


def _is_transient_503(error: Exception) -> bool:
    """Recognize Gemini service-unavailable failures without retrying others."""
    status_code = getattr(error, "status_code", None)
    if status_code == 503:
        return True
    code = getattr(error, "code", None)
    if code == 503 or str(code).casefold() in {"503", "unavailable"}:
        return True
    message = str(error).casefold()
    return "503" in message and "unavailable" in message


def create_gemini_client(model: str | None = None) -> GeminiClient:
    """Create a Gemini client using the local environment configuration."""
    return GeminiClient(model=model)
