import unittest

from src.product_intelligence.delivery_output import map_verified_source_content_to_delivery
from src.product_intelligence.delivery_schema import DeliverySchema
from src.product_intelligence.reference_data import UOMReference
from src.product_intelligence.verified_source_content import (
    SourceLink,
    StructuredProductData,
    VerifiedSourceContent,
)


DELIVERY_FIELDS = (
    "MFR URL",
    "Ref URL 1",
    "Ref URL 2",
    "Product Name",
    "MARKETING_DESCRIPTION",
    "ITEM_FEATURES_1",
    "ITEM_FEATURES_2",
    "Product Image",
    "Alternate Image 1",
    "Actual Image (Yes/No)",
    "SDS",
    "Specification Sheet",
    "Instruction/Installation Manual",
    "Video Link",
    "Video Link 1",
    "UPC",
    "EAN",
    "GTIN",
    "UNSPSC",
    "LENGTH",
    "LENGTH_UOM",
    "WEIGHT",
    "WEIGHT_UOM",
    "Warranty",
    "Selling Qty",
    "Selling UOM",
    "Standard Packaging Information",
)


class VerifiedContentDeliveryTests(unittest.TestCase):
    def test_verified_content_maps_to_high_value_delivery_fields(self) -> None:
        schema = DeliverySchema(DELIVERY_FIELDS)
        row = schema.empty_row()
        content = VerifiedSourceContent(
            canonical_url="https://manufacturer.example/product/MODEL-123",
            source_type="web",
            product_name="Example Product",
            description="Official description",
            features=["Feature one", "Feature two"],
            links=[
                SourceLink(
                    url="https://manufacturer.example/manuals/safety-sds.pdf",
                    kind="document",
                    text="Safety Data Sheet",
                ),
                SourceLink(
                    url="https://manufacturer.example/manuals/specification.pdf",
                    kind="document",
                    text="Specification Sheet",
                ),
                SourceLink(
                    url="https://manufacturer.example/manuals/install.pdf",
                    kind="document",
                    text="Installation Manual",
                ),
            ],
            image_urls=[
                "https://manufacturer.example/images/main.jpg",
                "https://manufacturer.example/images/alternate.jpg",
            ],
            video_urls=[
                "https://video.example/one",
                "https://video.example/two",
            ],
        )

        result = map_verified_source_content_to_delivery(row, content, schema)

        self.assertEqual(result["MFR URL"], content.canonical_url)
        self.assertEqual(result["Product Name"], "Example Product")
        self.assertEqual(result["MARKETING_DESCRIPTION"], "Official description")
        self.assertEqual(result["ITEM_FEATURES_1"], "Feature one")
        self.assertEqual(result["ITEM_FEATURES_2"], "Feature two")
        self.assertEqual(result["Product Image"], content.image_urls[0])
        self.assertEqual(result["Alternate Image 1"], content.image_urls[1])
        self.assertEqual(result["Actual Image (Yes/No)"], "Yes")
        self.assertEqual(result["SDS"], content.links[0].url)
        self.assertEqual(result["Specification Sheet"], content.links[1].url)
        self.assertEqual(result["Instruction/Installation Manual"], content.links[2].url)
        self.assertEqual(result["Video Link"], content.video_urls[0])
        self.assertEqual(result["Video Link 1"], content.video_urls[1])

    def test_missing_content_does_not_populate_unrelated_fields(self) -> None:
        schema = DeliverySchema(DELIVERY_FIELDS)
        row = schema.empty_row()
        content = VerifiedSourceContent(
            canonical_url="https://manufacturer.example/product/MODEL-123",
            source_type="web",
        )

        result = map_verified_source_content_to_delivery(row, content, schema)

        self.assertEqual(result["MFR URL"], content.canonical_url)
        self.assertEqual(result["Product Name"], "")
        self.assertEqual(result["ITEM_FEATURES_1"], "")
        self.assertEqual(result["Product Image"], "")
        self.assertEqual(result["SDS"], "")
        self.assertEqual(result["Video Link"], "")

    def test_structured_fields_require_approved_uoms(self) -> None:
        schema = DeliverySchema(DELIVERY_FIELDS)
        content = VerifiedSourceContent(
            canonical_url="https://manufacturer.example/product/MODEL-123",
            source_type="web",
            structured=StructuredProductData(
                upc="012345678905",
                length="10",
                length_uom="inches",
                weight="4.5",
                weight_uom="unknown-unit",
                warranty="5 years limited",
                selling_qty="4",
                selling_uom="each",
                packaging_information="Four units per carton",
            ),
        )

        result = map_verified_source_content_to_delivery(
            schema.empty_row(),
            content,
            schema,
            uom_reference=UOMReference({"IN": ("in", "inch", "inches"), "EA": ("each",)}),
        )

        self.assertEqual(result["UPC"], "012345678905")
        self.assertEqual(result["LENGTH"], "10")
        self.assertEqual(result["LENGTH_UOM"], "IN")
        self.assertEqual(result["WEIGHT"], "")
        self.assertEqual(result["WEIGHT_UOM"], "")
        self.assertEqual(result["Warranty"], "5 years limited")
        self.assertEqual(result["Selling Qty"], "4")
        self.assertEqual(result["Selling UOM"], "EA")
        self.assertEqual(result["Standard Packaging Information"], "Four units per carton")


if __name__ == "__main__":
    unittest.main()
