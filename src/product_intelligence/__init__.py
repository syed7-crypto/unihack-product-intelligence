"""UniHack 2026 AI Product Intelligence application package."""

from .attribute_extraction import (
    AttributeEvidence,
    AttributeExtractionError,
    AttributeExtractionResult,
    ExtractedAttribute,
    extract_attribute_values,
)
from .cross_source_validation import (
    ConflictInfo,
    CrossSourceValidationError,
    CrossSourceValidationResult,
    SourceAttributeValue,
    ValidatedAttribute,
    normalize_for_comparison,
    validate_cross_source,
)

__all__ = [
    "AttributeEvidence",
    "AttributeExtractionError",
    "AttributeExtractionResult",
    "ExtractedAttribute",
    "extract_attribute_values",
    "ConflictInfo",
    "CrossSourceValidationError",
    "CrossSourceValidationResult",
    "SourceAttributeValue",
    "ValidatedAttribute",
    "normalize_for_comparison",
    "validate_cross_source",
]
