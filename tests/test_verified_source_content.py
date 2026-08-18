import unittest

from src.product_intelligence.manufacturer_enrichment import ManufacturerSource
from src.product_intelligence.verified_source_content import (
    extract_verified_source_content,
)


SOURCE_URL = "https://manufacturer.example/products/MODEL-123"


FIXTURE_HTML = """
<html>
  <head>
    <title>Example Product | Official</title>
    <meta name="description" content="Official product description.">
    <meta itemprop="brand" content="Example Brand">
  </head>
  <body>
    <h1>Example Product Name</h1>
    <span itemprop="mpn">MODEL-123</span>
    <section id="description"><p>Long product description.</p></section>
    <section id="features">
      <ul><li>Feature one</li><li>Feature two</li></ul>
    </section>
    <section id="specifications">
      <table><tr><td>Voltage</td><td>120 V</td></tr></table>
    </section>
    <a href="/manuals/spec.pdf">Specification PDF</a>
    <a href="/catalog">Product catalog</a>
    <a href="https://www.youtube.com/watch?v=example">Product video</a>
    <img src="/images/product.jpg">
    <video src="/video/product.mp4"></video>
  </body>
</html>
"""


def verified_source(content: str) -> ManufacturerSource:
    return ManufacturerSource(
        url=SOURCE_URL,
        source_type="web",
        manufacturer_domain="manufacturer.example",
        source_name="official-product.html",
        content=content,
        exact_mpn_verified=True,
    )


class VerifiedSourceContentTests(unittest.TestCase):
    def test_extracts_structured_content_and_related_urls(self) -> None:
        result = extract_verified_source_content(verified_source(FIXTURE_HTML))

        self.assertEqual(result.canonical_url, SOURCE_URL)
        self.assertEqual(result.source_type, "web")
        self.assertEqual(result.page_title, "Example Product | Official")
        self.assertEqual(result.product_name, "Example Product Name")
        self.assertEqual(result.manufacturer_brand_text, "Example Brand")
        self.assertEqual(result.mpn_model_text, "MODEL-123")
        self.assertEqual(result.description, "Official product description.")
        self.assertEqual(result.features, ["Feature one", "Feature two"])
        self.assertIn("Voltage 120 V", result.specification_text)
        self.assertIn("https://manufacturer.example/manuals/spec.pdf", result.document_urls)
        self.assertIn("https://manufacturer.example/images/product.jpg", result.image_urls)
        self.assertIn("https://www.youtube.com/watch?v=example", result.video_urls)
        self.assertIn("https://manufacturer.example/video/product.mp4", result.video_urls)
        self.assertEqual(
            next(link for link in result.links if link.url.endswith("spec.pdf")).kind,
            "document",
        )

    def test_missing_sections_remain_absent(self) -> None:
        result = extract_verified_source_content(
            verified_source("<html><title>Minimal</title><h1>MODEL-123</h1></html>")
        )

        self.assertEqual(result.page_title, "Minimal")
        self.assertEqual(result.product_name, "MODEL-123")
        self.assertIsNone(result.manufacturer_brand_text)
        self.assertIsNone(result.description)
        self.assertEqual(result.features, [])
        self.assertEqual(result.specification_text, [])
        self.assertEqual(result.links, [])
        self.assertEqual(result.image_urls, [])
        self.assertEqual(result.document_urls, [])
        self.assertEqual(result.video_urls, [])

    def test_unverified_source_cannot_be_extracted(self) -> None:
        with self.assertRaises(ValueError):
            ManufacturerSource(
                url=SOURCE_URL,
                source_type="web",
                manufacturer_domain="manufacturer.example",
                source_name="unverified.html",
                content=FIXTURE_HTML,
                exact_mpn_verified=False,
            )


if __name__ == "__main__":
    unittest.main()
