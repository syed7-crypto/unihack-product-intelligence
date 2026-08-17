import unittest
from types import SimpleNamespace

from src.product_intelligence.attribute_extraction import AttributeEvidence
from src.product_intelligence.catalogue_enrichment import _map_validated_attributes
from src.product_intelligence.controlled_attribute_mapping import (
    ControlledAttributeMapping,
    ControlledAttributeMappingRegistry,
)
from src.product_intelligence.cross_source_validation import (
    SourceAttributeValue,
    ValidatedAttribute,
)
from src.product_intelligence.product_identification import (
    AttributeDefinition,
    ProductIdentificationResult,
)
from src.product_intelligence.confidence_scoring import ConfidenceAssessment


def make_pipeline(attributes: list[ValidatedAttribute], *, low: set[str] | None = None):
    low = low or set()
    definitions = [
        AttributeDefinition(name=item.name, label=item.name.replace("_", " ").title())
        for item in attributes
    ]
    identification = ProductIdentificationResult(
        product_type="Test Product",
        product_category="Test",
        attributes=definitions,
    )
    confidence = [
        ConfidenceAssessment(
            name=item.name,
            score=0.3 if item.name in low else 0.6,
            level="low" if item.name in low else "medium",
            reasons=["deterministic test"],
        )
        for item in attributes
    ]
    return SimpleNamespace(
        product_identification=identification,
        validation=SimpleNamespace(attributes=attributes),
        confidence=SimpleNamespace(attributes=confidence),
    )


def found_attribute(name: str, value: str | None = None, status: str = "single_source"):
    if status == "not_found":
        return ValidatedAttribute(name=name, status="not_found")
    evidence = AttributeEvidence(
        source_id="test-source",
        source_name="test.txt",
        location="document",
        quote=f"{name}: {value}",
    )
    return ValidatedAttribute(
        name=name,
        status=status,
        values=[SourceAttributeValue(value=value or "value", evidence=evidence)],
    )


class SequentialDeliverySlotTests(unittest.TestCase):
    def map_attributes(self, attributes, **kwargs):
        row = {}
        diagnostics = _map_validated_attributes(
            row,
            make_pipeline(attributes, low=kwargs.pop("low", set())),
            kwargs.pop("mappings", ControlledAttributeMappingRegistry()),
            kwargs.pop("attribute_reference", None),
            kwargs.pop("uom_reference", None),
        )
        return row, diagnostics

    def test_one_and_three_validated_attributes_use_canonical_name_order(self):
        row, _ = self.map_attributes([found_attribute("first_value")])
        self.assertEqual(row["ATTRIBUTE_LABEL 1"], "First Value")
        self.assertEqual(row["ATTRIBUTE_VALUE 1"], "value")

        row, _ = self.map_attributes(
            [found_attribute("first_value", "one"), found_attribute("second_value", "two"), found_attribute("third_value", "three")]
        )
        self.assertEqual([row[f"ATTRIBUTE_VALUE {n}"] for n in range(1, 4)], ["one", "two", "three"])

    def test_different_input_orders_produce_identical_slots(self):
        first, _ = self.map_attributes([
            found_attribute("wheel_thickness", "thin"),
            found_attribute("arbor_size", "small"),
            found_attribute("wheel_diameter", "large"),
        ])
        second, _ = self.map_attributes([
            found_attribute("wheel_diameter", "large"),
            found_attribute("wheel_thickness", "thin"),
            found_attribute("arbor_size", "small"),
        ])
        self.assertEqual(first, second)
        self.assertEqual(
            [first[f"ATTRIBUTE_LABEL {n}"] for n in range(1, 4)],
            ["Arbor Size", "Wheel Diameter", "Wheel Thickness"],
        )

    def test_fifty_are_exported_and_fifty_first_is_overflow(self):
        attributes = [found_attribute(f"attribute_{n:02}", str(n)) for n in range(1, 52)]
        row, diagnostics = self.map_attributes(attributes)
        self.assertEqual(row["ATTRIBUTE_VALUE 1"], "1")
        self.assertEqual(row["ATTRIBUTE_VALUE 50"], "50")
        self.assertNotIn("ATTRIBUTE_VALUE 51", row)
        overflow = [item for item in diagnostics if item.attribute_name == "attribute_51"]
        self.assertEqual(len(overflow), 1)
        self.assertIn("ATTRIBUTE_SLOT_LIMIT_EXCEEDED", overflow[0].reason)

    def test_repeated_input_and_legacy_slot_metadata_are_deterministic(self):
        attributes = [found_attribute("wheel_diameter", "6-1/2 in"), found_attribute("arbor_size", "5/8 in")]
        mappings = ControlledAttributeMappingRegistry(
            mappings=[
                ControlledAttributeMapping(
                    internal_attribute_name="wheel_diameter",
                    delivery_label="Wheel Diameter",
                    slot=16,
                ),
                ControlledAttributeMapping(
                    internal_attribute_name="arbor_size",
                    delivery_label="Arbor Size",
                    slot=18,
                ),
            ]
        )
        first, _ = self.map_attributes(attributes, mappings=mappings)
        second, _ = self.map_attributes(attributes, mappings=mappings)
        self.assertEqual(first, second)
        self.assertEqual(first["ATTRIBUTE_LABEL 1"], "Arbor Size")
        self.assertEqual(first["ATTRIBUTE_LABEL 2"], "Wheel Diameter")
        self.assertEqual(first["ATTRIBUTE_VALUE 1"], "5/8 in")

    def test_not_found_rejected_and_low_confidence_does_not_consume_slots(self):
        attributes = [
            found_attribute("missing_value", status="not_found"),
            found_attribute("low_value", "low"),
            found_attribute("accepted_value", "accepted"),
        ]
        row, diagnostics = self.map_attributes(attributes, low={"low_value"})
        self.assertEqual(row["ATTRIBUTE_VALUE 1"], "accepted")
        self.assertTrue(all(item.slot is None for item in diagnostics if item.status == "skipped"))

    def test_conflict_does_not_consume_slot(self):
        conflict = found_attribute("conflicting_value", "one", status="conflict").model_copy(
            update={
                "values": [
                    found_attribute("conflicting_value", "one").values[0],
                    found_attribute("conflicting_value", "two").values[0],
                ]
            }
        )
        row, _ = self.map_attributes([conflict, found_attribute("accepted_value", "accepted")])
        self.assertEqual(row["ATTRIBUTE_VALUE 1"], "accepted")

    def test_no_fixed_mapping_is_required_for_valid_attribute(self):
        row, diagnostics = self.map_attributes([found_attribute("unmapped_attribute", "supported")])
        self.assertEqual(row["ATTRIBUTE_LABEL 1"], "Unmapped Attribute")
        self.assertEqual(diagnostics[0].status, "mapped")
        self.assertNotEqual(diagnostics[0].reason, "ATTRIBUTE_MAPPING_MISSING")


if __name__ == "__main__":
    unittest.main()
