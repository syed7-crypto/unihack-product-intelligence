import unittest
from pathlib import Path

from src.product_intelligence.delivery_schema import load_delivery_schema


DELIVERY_CSV = Path(r"C:\Users\syed7\Downloads\Unihack_ Expected Output - Delivery Format.csv")


class DeliveryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not DELIVERY_CSV.exists():
            raise unittest.SkipTest("The supplied delivery CSV is not available.")
        cls.schema = load_delivery_schema(DELIVERY_CSV)

    def test_official_schema_has_exact_order_and_high_value_fields(self) -> None:
        columns = self.schema.columns
        self.assertEqual(len(columns), 252)
        self.assertEqual(len(self.schema.empty_row()), 252)

        for number in range(1, 51):
            start = columns.index(f"ATTRIBUTE_LABEL {number}")
            self.assertEqual(
                columns[start : start + 3],
                (
                    f"ATTRIBUTE_LABEL {number}",
                    f"ATTRIBUTE_VALUE {number}",
                    f"ATTRIBUTE_UOM {number}",
                ),
            )

        for field in (
            "MFR URL",
            "Ref URL 1",
            "MARKETING_DESCRIPTION",
            "ITEM_FEATURES_1",
            "Product Name",
            "Product Image",
            "SDS",
            "Specification Sheet",
            "Video Link",
            "UPC",
            "LENGTH",
            "LENGTH_UOM",
        ):
            self.assertIn(field, columns)

    def test_empty_row_is_exactly_schema_ordered(self) -> None:
        row = self.schema.empty_row()
        self.assertEqual(list(row), list(self.schema.columns))
        self.assertTrue(all(value == "" for value in row.values()))


if __name__ == "__main__":
    unittest.main()
