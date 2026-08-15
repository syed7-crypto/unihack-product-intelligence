"""Small, deterministic normalization helpers for physical quantities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class NormalizedMeasurement:
    """A parsed quantity in a canonical unit, for comparison only."""

    dimension: str
    canonical_unit: str
    canonical_value: Decimal


@dataclass(frozen=True)
class UnitDefinition:
    dimension: str
    canonical_unit: str
    multiplier: Decimal
    offset: Decimal = Decimal("0")


_NUMBER_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_MEASUREMENT_PATTERN = re.compile(
    rf"^\s*(?P<number>{_NUMBER_PATTERN})\s*(?P<unit>[^\d\s]+(?:\s+[^\d\s]+)?)\s*$",
    re.IGNORECASE,
)


def _definition(dimension: str, canonical_unit: str, multiplier: str) -> UnitDefinition:
    return UnitDefinition(dimension, canonical_unit, Decimal(multiplier))


_UNITS: dict[str, UnitDefinition] = {
    # Length → millimetres
    "mm": _definition("length", "mm", "1"),
    "cm": _definition("length", "mm", "10"),
    "m": _definition("length", "mm", "1000"),
    "km": _definition("length", "mm", "1000000"),
    "in": _definition("length", "mm", "25.4"),
    "inch": _definition("length", "mm", "25.4"),
    "inches": _definition("length", "mm", "25.4"),
    "ft": _definition("length", "mm", "304.8"),
    "foot": _definition("length", "mm", "304.8"),
    "feet": _definition("length", "mm", "304.8"),
    # Mass → grams
    "mg": _definition("mass", "g", "0.001"),
    "g": _definition("mass", "g", "1"),
    "kg": _definition("mass", "g", "1000"),
    "lb": _definition("mass", "g", "453.59237"),
    "lbs": _definition("mass", "g", "453.59237"),
    # Pressure → pascals
    "pa": _definition("pressure", "Pa", "1"),
    "kpa": _definition("pressure", "Pa", "1000"),
    "mpa": _definition("pressure", "Pa", "1000000"),
    "bar": _definition("pressure", "Pa", "100000"),
    "psi": _definition("pressure", "Pa", "6894.757293168"),
    # Volume → millilitres
    "ml": _definition("volume", "mL", "1"),
    "l": _definition("volume", "mL", "1000"),
    "gal": _definition("volume", "mL", "3785.411784"),
    # Power → watts
    "w": _definition("power", "W", "1"),
    "kw": _definition("power", "W", "1000"),
    # Voltage → volts
    "v": _definition("voltage", "V", "1"),
    "kv": _definition("voltage", "V", "1000"),
    "mv": _definition("voltage", "V", "0.001"),
    # Current → amps
    "a": _definition("current", "A", "1"),
    "ma": _definition("current", "A", "0.001"),
    # Frequency → hertz
    "hz": _definition("frequency", "Hz", "1"),
    "khz": _definition("frequency", "Hz", "1000"),
    "mhz": _definition("frequency", "Hz", "1000000"),
    # Temperature → degrees Celsius. These are affine conversions.
    "°c": _definition("temperature", "°C", "1"),
    "c": _definition("temperature", "°C", "1"),
    "celsius": _definition("temperature", "°C", "1"),
    "°f": UnitDefinition("temperature", "°C", Decimal("5") / Decimal("9"), Decimal("-32")),
    "f": UnitDefinition("temperature", "°C", Decimal("5") / Decimal("9"), Decimal("-32")),
    "fahrenheit": UnitDefinition(
        "temperature", "°C", Decimal("5") / Decimal("9"), Decimal("-32")
    ),
}


def normalize_measurement(value: str) -> NormalizedMeasurement | None:
    """Parse and convert one complete numeric-plus-supported-unit value.

    Returns ``None`` for arbitrary text, compound dimensions, unknown units, or
    malformed numbers. No normalization is applied to the caller's raw value.
    """
    match = _MEASUREMENT_PATTERN.fullmatch(value)
    if not match:
        return None

    try:
        number = Decimal(match.group("number"))
    except InvalidOperation:
        return None

    unit_text = re.sub(r"\s+", " ", match.group("unit").strip()).casefold()
    definition = _UNITS.get(unit_text)
    if definition is None:
        return None

    if definition.dimension == "temperature" and unit_text in {
        "°f",
        "f",
        "fahrenheit",
    }:
        canonical_value = (number + definition.offset) * definition.multiplier
    else:
        canonical_value = number * definition.multiplier + definition.offset

    return NormalizedMeasurement(
        dimension=definition.dimension,
        canonical_unit=definition.canonical_unit,
        canonical_value=canonical_value,
    )


def measurements_equivalent(
    left: str,
    right: str,
    *,
    relative_tolerance: Decimal = Decimal("0.0001"),
    absolute_tolerance: Decimal = Decimal("0.000000001"),
) -> bool | None:
    """Compare parsed measurements, or return ``None`` for fallback handling.

    ``False`` is returned for different dimensions or different quantities;
    ``None`` means at least one value was not safely unit-normalized.
    """
    left_measurement = normalize_measurement(left)
    right_measurement = normalize_measurement(right)
    if left_measurement is None or right_measurement is None:
        return None
    if left_measurement.dimension != right_measurement.dimension:
        return False

    difference = abs(left_measurement.canonical_value - right_measurement.canonical_value)
    scale = max(abs(left_measurement.canonical_value), abs(right_measurement.canonical_value))
    tolerance = max(absolute_tolerance, scale * relative_tolerance)
    return difference <= tolerance
