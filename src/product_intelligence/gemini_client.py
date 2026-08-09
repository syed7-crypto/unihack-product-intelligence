"""Small reusable client for calling the Gemini API."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai


DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"


class GeminiClient:
    """Configured Gemini text-generation client."""

    def __init__(self, model: str | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        load_dotenv(project_root / ".env")

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured. Add it to the local .env file."
            )

        self.model = model or os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
        self._client = genai.Client(api_key=api_key)

    def generate_text(self, prompt: str) -> str:
        """Send a text prompt and return the generated response text."""
        if not prompt.strip():
            raise ValueError("The Gemini prompt must not be empty.")

        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        response_text = response.text
        if not response_text:
            raise RuntimeError("Gemini returned an empty response.")
        return response_text


def create_gemini_client(model: str | None = None) -> GeminiClient:
    """Create a Gemini client using the local environment configuration."""
    return GeminiClient(model=model)
