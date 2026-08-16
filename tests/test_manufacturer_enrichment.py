import unittest
from unittest.mock import patch

from pypdf import PdfWriter

from src.product_intelligence.extraction import NormalizedSource, SourceLocation
from src.product_intelligence.manufacturer_enrichment import (
    ManufacturerEnrichmentProvider,
    ManufacturerSource,
    RetrievedPayload,
)


MPN = "PDSH4816AF"
WEB_URL = "https://www.frigidaire.com/en/p/owner-center/product-support/PDSH4816AF"
PDF_URL = "https://frigidaire.bynder.com/specs/PDSH4816A_EN-pdf.pdf"


def payload(body: bytes, content_type: str = "text/html", status: int = 200) -> RetrievedPayload:
    return RetrievedPayload(status, {"content-type": content_type}, body)


def minimal_pdf() -> bytes:
    """Create a valid local PDF; extraction is verified by the production code."""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = __import__("io").BytesIO()
    writer.write(output)
    return output.getvalue()


class ManufacturerEnrichmentTests(unittest.TestCase):
    def test_approved_web_domain_and_exact_mpn_are_accepted(self) -> None:
        fetcher = lambda url, timeout: payload(
            b"<html><title>Frigidaire Product</title><h1>PDSH4816AF</h1>"
            b"<h2>Specifications</h2><p>Voltage Rating: 120 V</p></html>"
        )
        provider = ManufacturerEnrichmentProvider(fetcher=fetcher)

        result = provider.retrieve_source(WEB_URL, MPN)

        self.assertTrue(result.success)
        assert result.source is not None
        self.assertEqual(result.source.source_type, "web")
        self.assertEqual(result.source.manufacturer_domain, "www.frigidaire.com")
        normalized = provider.to_normalized_source(result.source)
        self.assertEqual(normalized.source_type, "web")
        self.assertIn(MPN, normalized.extracted_text)
        self.assertIn("document", {location.label for location in normalized.locations})
        self.assertIn("Specifications", {location.label for location in normalized.locations})

    def test_approved_bynder_pdf_domain_is_accepted_and_delegates_to_pdf_extractor(self) -> None:
        pdf_content = minimal_pdf()
        extracted = NormalizedSource(
            source_id="pdf-source-id",
            source_type="pdf",
            source_name="temporary.pdf",
            extracted_text=f"Available Products: {MPN}",
            locations=(SourceLocation("page 1", 1),),
        )
        fetcher = lambda url, timeout: payload(pdf_content, "application/pdf")
        provider = ManufacturerEnrichmentProvider(fetcher=fetcher)

        with patch(
            "src.product_intelligence.manufacturer_enrichment.extract_pdf",
            return_value=extracted,
        ) as extract_pdf_mock:
            result = provider.retrieve_source(PDF_URL, MPN)
            self.assertTrue(result.success)
            assert result.source is not None
            self.assertEqual(result.source.manufacturer_domain, "frigidaire.bynder.com")
            normalized = provider.to_normalized_source(result.source)
            self.assertEqual(normalized.locations[0].label, "page 1")
            self.assertEqual(normalized.locations[0].page_number, 1)

        self.assertEqual(extract_pdf_mock.call_count, 2)

    def test_unapproved_domain_is_rejected_before_fetch(self) -> None:
        fetcher = unittest.mock.Mock()
        provider = ManufacturerEnrichmentProvider(fetcher=fetcher)

        result = provider.retrieve_source("https://example.com/PDSH4816AF", MPN)

        self.assertFalse(result.success)
        self.assertIn("allowlist", result.error or "")
        fetcher.assert_not_called()

    def test_similar_mpn_does_not_satisfy_exact_verification(self) -> None:
        provider = ManufacturerEnrichmentProvider(
            fetcher=lambda url, timeout: payload(b"<h1>PDSH4816BF</h1>")
        )

        result = provider.retrieve_source(WEB_URL, MPN)

        self.assertFalse(result.success)
        self.assertIn("Exact MPN", result.error or "")
        self.assertIsNone(result.source)

    def test_mpn_absent_and_empty_response_are_explicit_failures(self) -> None:
        for response in (payload(b"<p>Other product</p>"), payload(b"")):
            provider = ManufacturerEnrichmentProvider(fetcher=lambda url, timeout, r=response: r)
            result = provider.retrieve_source(WEB_URL, MPN)
            self.assertFalse(result.success)
            self.assertIsNotNone(result.error)
            self.assertIsNone(result.source)

    def test_http_failure_is_explicit(self) -> None:
        provider = ManufacturerEnrichmentProvider(
            fetcher=lambda url, timeout: payload(b"failure", status=404)
        )

        result = provider.retrieve_source(WEB_URL, MPN)

        self.assertFalse(result.success)
        self.assertIn("404", result.error or "")

    def test_url_and_domain_metadata_are_preserved(self) -> None:
        provider = ManufacturerEnrichmentProvider(
            fetcher=lambda url, timeout: payload(b"<title>Exact Product</title><p>PDSH4816AF</p>")
        )

        result = provider.retrieve_source(WEB_URL, MPN)

        assert result.source is not None
        self.assertEqual(result.source.url, WEB_URL)
        self.assertEqual(result.source.source_name, "Exact Product")
        self.assertEqual(result.source.manufacturer_domain, "www.frigidaire.com")
        self.assertTrue(result.source.exact_mpn_verified)

    def test_retrieval_does_not_create_a_gemini_client_or_call_gemini(self) -> None:
        provider = ManufacturerEnrichmentProvider(
            fetcher=lambda url, timeout: payload(b"<p>PDSH4816AF</p>")
        )

        with patch("src.product_intelligence.gemini_client.create_gemini_client") as client:
            result = provider.retrieve_source(WEB_URL, MPN)

        self.assertTrue(result.success)
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
