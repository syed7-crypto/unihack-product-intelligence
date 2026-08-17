import unittest

from src.product_intelligence.attribute_mapping_coverage import task42_candidate_mappings


class AttributeMappingCoverageTests(unittest.TestCase):
    def test_candidate_profile_is_reusable_and_explicitly_non_official(self):
        registry = task42_candidate_mappings()

        self.assertEqual(len(registry.mappings), 6)
        self.assertEqual([mapping.slot for mapping in registry.mappings], [16, 17, 18, 19, 20, 21])
        self.assertTrue(all(mapping.mapping_source == "mock" for mapping in registry.mappings))
        self.assertTrue(all(mapping.governance_status == "candidate" for mapping in registry.mappings))
        self.assertTrue(all("official" in mapping.governance_reason for mapping in registry.mappings))

    def test_observed_names_and_only_explicit_aliases_resolve(self):
        registry = task42_candidate_mappings()

        self.assertEqual(registry.resolve("wheel_diameter").slot, 16)
        self.assertEqual(registry.resolve("thickness").canonical_name, "wheel_thickness")
        self.assertEqual(registry.resolve("blade count").slot, 20)
        self.assertIsNone(registry.resolve("diameter-ish"))

    def test_candidate_profile_does_not_contain_product_or_website_keys(self):
        registry = task42_candidate_mappings()
        for mapping in registry.mappings:
            self.assertFalse(hasattr(mapping, "mfg_part_num"))
            self.assertFalse(hasattr(mapping, "website"))


if __name__ == "__main__":
    unittest.main()
