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

    def test_extracts_explicit_structured_fields_without_guessing(self) -> None:
        html = """
        <html><body>
          <h1>MODEL-123</h1>
          <p>UPC: 012345678905</p>
          <p>EAN: 4006381333931</p>
          <p>GTIN: 00012345600012</p>
          <p>UNSPSC: 24111503</p>
          <p>Length: 10 in</p>
          <p>Height: 20 cm</p>
          <p>Width: 3.5 ft</p>
          <p>Weight: 4.5 lb</p>
          <p>Volume: 2 L</p>
          <p>Warranty: 5 years limited</p>
          <p>Selling Quantity: 4 each</p>
          <p>Standard Packaging Information: Four units per carton</p>
        </body></html>
        """

        result = extract_verified_source_content(verified_source(html))

        self.assertEqual(result.structured.upc, "012345678905")
        self.assertEqual(result.structured.ean, "4006381333931")
        self.assertEqual(result.structured.gtin, "00012345600012")
        self.assertEqual(result.structured.unspsc, "24111503")
        self.assertEqual(result.structured.length, "10")
        self.assertEqual(result.structured.length_uom, "in")
        self.assertEqual(result.structured.height, "20")
        self.assertEqual(result.structured.height_uom, "cm")
        self.assertEqual(result.structured.weight, "4.5")
        self.assertEqual(result.structured.weight_uom, "lb")
        self.assertEqual(result.structured.warranty, "5 years limited")
        self.assertEqual(result.structured.selling_qty, "4")
        self.assertEqual(result.structured.selling_uom, "each")
        self.assertEqual(
            result.structured.packaging_information,
            "Four units per carton",
        )


if __name__ == "__main__":
    unittest.main()
