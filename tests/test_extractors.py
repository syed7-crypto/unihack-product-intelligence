"""Tests for the initial input extraction layer."""

import json
import tempfile
import unittest
from pathlib import Path

from src.product_intelligence.extraction import (
    ExtractionError,
    NormalizedSource,
    extract_file,
    extract_json,
    extract_pdf,
    extract_txt,
)


PROJECT_ROOT = Path(__file__).parents[1]
SAMPLES = PROJECT_ROOT / "samples"


class ExtractionTests(unittest.TestCase):
    def test_txt_extraction(self) -> None:
        source = extract_txt(SAMPLES / "industrial_valve.txt")
        self.assertEqual(source.source_type, "txt")
        self.assertIn("Pressure rating: 150 PSI", source.extracted_text)

    def test_json_extraction(self) -> None:
        source = extract_json(SAMPLES / "industrial_valve.json")
        self.assertEqual(source.source_type, "json")
        self.assertIn('"pressure_rating_psi": 150', source.extracted_text)

    def test_pdf_extraction(self) -> None:
        source = extract_pdf(SAMPLES / "industrial_valve.pdf")
        self.assertEqual(source.source_type, "pdf")
        self.assertIn("Pressure Rating: 150 PSI", source.extracted_text)
        self.assertEqual(source.locations[0].page_number, 1)

    def test_normalized_output_structure(self) -> None:
        source = extract_file(SAMPLES / "industrial_valve.txt")
        self.assertIsInstance(source, NormalizedSource)
        self.assertTrue(source.source_id)
        self.assertTrue(source.source_name)
        self.assertIsInstance(source.extracted_text, str)
        self.assertTrue(source.locations)

    def test_invalid_json_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text('{"product_name": ', encoding="utf-8")
            with self.assertRaisesRegex(ExtractionError, "Could not parse JSON"):
                extract_json(path)

    def test_unsupported_extension_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.csv"
            path.write_text("not supported", encoding="utf-8")
            with self.assertRaisesRegex(ExtractionError, "Unsupported file type"):
                extract_file(path)

    def test_empty_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.txt"
            path.touch()
            with self.assertRaisesRegex(ExtractionError, "is empty"):
                extract_txt(path)


if __name__ == "__main__":
    unittest.main()
