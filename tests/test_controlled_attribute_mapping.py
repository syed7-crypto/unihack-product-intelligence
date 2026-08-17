import unittest

from pydantic import ValidationError

from src.product_intelligence.controlled_attribute_mapping import (
    ControlledAttributeMapping,
    ControlledAttributeMappingRegistry,
    resolve_controlled_attribute_mapping,
)


def mapping(**overrides):
    values = {
        "internal_attribute_name": "blade_span",
        "canonical_attribute_name": "blade_span",
        "delivery_label": "Blade Span",
        "aliases": ("blade span", "fan diameter"),
        "applicable_categories": ("Ceiling Fans",),
        "slot": 1,
    }
    values.update(overrides)
    return ControlledAttributeMapping(**values)


class ControlledAttributeMappingTests(unittest.TestCase):
    def test_exact_canonical_and_explicit_alias_resolution(self):
        registry = ControlledAttributeMappingRegistry(mappings=[mapping()])

        self.assertEqual(registry.resolve("blade_span", category="Ceiling Fans").slot, 1)
        self.assertEqual(registry.resolve("fan diameter", category="Ceiling Fans").canonical_name, "blade_span")

    def test_unknown_alias_is_not_resolved(self):
        registry = ControlledAttributeMappingRegistry(mappings=[mapping()])
        self.assertIsNone(registry.resolve("blade size", category="Ceiling Fans"))

    def test_taxonomy_or_category_scope_is_deterministic(self):
        registry = ControlledAttributeMappingRegistry(mappings=[mapping()])
        self.assertIsNone(registry.resolve("blade_span", category="Dishwasher"))
        self.assertIsNotNone(registry.resolve("blade_span", category="ceiling fans"))

    def test_duplicate_slots_are_rejected(self):
        with self.assertRaises((ValidationError, ValueError)):
            ControlledAttributeMappingRegistry(
                mappings=[mapping(), mapping(canonical_attribute_name="other", internal_attribute_name="other")]
            )

    def test_invalid_slot_is_rejected(self):
        with self.assertRaises((ValidationError, ValueError)):
            mapping(slot=51)

    def test_duplicate_canonical_or_ambiguous_alias_is_rejected(self):
        with self.assertRaises((ValidationError, ValueError)):
            ControlledAttributeMappingRegistry(
                mappings=[mapping(), mapping(slot=2, aliases=("blade_span",))]
            )

    def test_sequence_resolver_has_no_fuzzy_matching(self):
        result = resolve_controlled_attribute_mapping(
            "Blade Span", [mapping()], category="Ceiling Fans"
        )
        self.assertEqual(result.delivery_label, "Blade Span")
        self.assertIsNone(
            resolve_controlled_attribute_mapping(
                "blade span overall", [mapping()], category="Ceiling Fans"
            )
        )

    def test_delivery_slot_is_governed_configuration_not_extracted_input(self):
        controlled = mapping(slot=7)
        resolved = ControlledAttributeMappingRegistry(mappings=[controlled]).resolve(
            "fan diameter", category="Ceiling Fans"
        )
        self.assertEqual(resolved.slot, 7)
        self.assertFalse(hasattr(resolved, "gemini_delivery_slot"))


if __name__ == "__main__":
    unittest.main()
