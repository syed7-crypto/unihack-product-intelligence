"""Tests for deterministic physical-unit normalization and comparison."""

import unittest
from decimal import Decimal

from src.product_intelligence.unit_normalization import (
    measurements_equivalent,
    normalize_measurement,
)


class UnitNormalizationTests(unittest.TestCase):
    def test_length_conversion(self) -> None:
        self.assertTrue(measurements_equivalent("2 m", "200 cm"))
        self.assertTrue(measurements_equivalent("1 inch", "25.4 mm"))
        self.assertEqual(normalize_measurement("2 m").canonical_unit, "mm")  # type: ignore[union-attr]
        self.assertEqual(normalize_measurement("2 m").canonical_value, Decimal("2000"))  # type: ignore[union-attr]

    def test_mass_conversion(self) -> None:
        self.assertTrue(measurements_equivalent("1 kg", "1000 g"))
        self.assertTrue(measurements_equivalent("1 lb", "453.59237 g"))

    def test_pressure_conversion(self) -> None:
        self.assertTrue(measurements_equivalent("150 psi", "10.342 bar"))
        self.assertFalse(measurements_equivalent("150 psi", "150 kPa"))

    def test_volume_conversion(self) -> None:
        self.assertTrue(measurements_equivalent("1 L", "1000 mL"))
        self.assertTrue(measurements_equivalent("1 gal", "3785.411784 mL"))

    def test_power_conversion(self) -> None:
        self.assertTrue(measurements_equivalent("1 kW", "1000 W"))

    def test_voltage_conversion(self) -> None:
        self.assertTrue(measurements_equivalent("1 kV", "1000 V"))
        self.assertTrue(measurements_equivalent("500 mV", "0.5 V"))

    def test_current_conversion(self) -> None:
        self.assertTrue(measurements_equivalent("1 A", "1000 mA"))

    def test_frequency_conversion(self) -> None:
        self.assertTrue(measurements_equivalent("1 MHz", "1000 kHz"))

    def test_temperature_conversion(self) -> None:
        self.assertTrue(measurements_equivalent("100 °C", "212 °F"))
        self.assertTrue(measurements_equivalent("100 Celsius", "212 Fahrenheit"))

    def test_different_dimensions_are_not_equivalent(self) -> None:
        self.assertFalse(measurements_equivalent("2 m", "2 kg"))

    def test_unsupported_values_use_fallback_signal(self) -> None:
        self.assertIsNone(measurements_equivalent("IP67", "IP67"))
        self.assertIsNone(measurements_equivalent("304 Stainless Steel", "304 SS"))
        self.assertIsNone(measurements_equivalent("2 m x 3 m", "6 m"))

    def test_tolerance_handles_small_conversion_rounding(self) -> None:
        self.assertTrue(measurements_equivalent("150 psi", "10.342 bar"))
        self.assertFalse(measurements_equivalent("150 psi", "10 bar"))


if __name__ == "__main__":
    unittest.main()
