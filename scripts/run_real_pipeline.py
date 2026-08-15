"""Manual real-Gemini end-to-end test for the controlled valve dataset."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make direct execution from the repository root work without changing the
# application package or requiring an installed distribution.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.product_intelligence.pipeline import (
    ProductIntelligencePipelineError,
    run_pipeline,
)


def main() -> None:
    sample_directory = PROJECT_ROOT / "samples" / "industrial_valve"
    source_files = [
        sample_directory / "valve_datasheet.pdf",
        sample_directory / "valve_description.txt",
        sample_directory / "valve_product.json",
    ]

    print("MANUAL REAL-API TEST: UniHack product intelligence pipeline")
    print("Using the three controlled industrial valve sample files.")
    print()

    try:
        # No client is passed intentionally: run_pipeline creates the existing
        # Gemini client, which reads GEMINI_API_KEY and GEMINI_MODEL from .env.
        result = run_pipeline(source_files)
    except ProductIntelligencePipelineError as error:
        print(f"Pipeline failed: {error}")
        raise SystemExit(1) from error

    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
