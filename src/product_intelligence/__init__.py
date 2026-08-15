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
from .confidence_scoring import (
    ConfidenceAssessment,
    ConfidenceScoringResult,
    calculate_confidence,
)
from .pipeline import (
    ProductIntelligencePipelineError,
    ProductIntelligenceResult,
    SourceSummary,
    run_pipeline,
)
from .unit_normalization import (
    NormalizedMeasurement,
    measurements_equivalent,
    normalize_measurement,
)
from .catalog_input import (
    CatalogInputError,
    CatalogInputRow,
    brand_candidate,
    is_placeholder_brand,
    load_catalog_rows,
    select_catalog_row,
)
from .delivery_schema import (
    DeliverySchema,
    DeliverySchemaError,
    EXPECTED_DELIVERY_COLUMN_COUNT,
    load_delivery_rows,
    load_delivery_schema,
    select_delivery_row,
)
from .delivery_output import (
    DeliveryComparison,
    DeliveryFieldDifference,
    compare_delivery_rows,
    map_raw_fields_to_delivery,
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
    "ConfidenceAssessment",
    "ConfidenceScoringResult",
    "calculate_confidence",
    "ProductIntelligencePipelineError",
    "ProductIntelligenceResult",
    "SourceSummary",
    "run_pipeline",
    "NormalizedMeasurement",
    "measurements_equivalent",
    "normalize_measurement",
    "CatalogInputError",
    "CatalogInputRow",
    "brand_candidate",
    "is_placeholder_brand",
    "load_catalog_rows",
    "select_catalog_row",
    "DeliverySchema",
    "DeliverySchemaError",
    "EXPECTED_DELIVERY_COLUMN_COUNT",
    "load_delivery_rows",
    "load_delivery_schema",
    "select_delivery_row",
    "DeliveryComparison",
    "DeliveryFieldDifference",
    "compare_delivery_rows",
    "map_raw_fields_to_delivery",
]
