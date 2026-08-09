"""Verify that the configured Gemini API can answer a harmless prompt."""

from src.product_intelligence.gemini_client import create_gemini_client


def main() -> int:
    try:
        client = create_gemini_client()
        response_text = client.generate_text("Reply with the single word: OK")
    except Exception:
        print("Gemini connection failed. Check the local configuration and network access.")
        return 1

    if not response_text.strip():
        print("Gemini connection failed: no response text was received.")
        return 1

    print(f"Gemini connection succeeded using {client.model}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
