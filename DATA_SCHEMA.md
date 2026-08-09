# Data Schema

## Design Goal

The output schema must support different product types without forcing every product into the same fixed set of fields.

## Core Idea

Each product should have:

- identity information
- a product category
- a dynamic list of relevant attributes
- values for attributes
- evidence for values
- confidence for values
- missing attributes
- conflicts when sources disagree

## Suggested JSON Structure

```json
{
  "product_id": "string",
  "product_name": "string",
  "product_category": "string",
  "source_summary": [
    {
      "source_id": "string",
      "source_type": "pdf|txt|json|csv|url",
      "source_name": "string"
    }
  ],
  "schema": {
    "attributes": [
      {
        "name": "pressure_rating",
        "label": "Pressure Rating",
        "type": "number|string|boolean|enum",
        "unit": "psi",
        "required": true,
        "description": "string"
      }
    ]
  },
  "attributes": {
    "pressure_rating": {
      "value": 150,
      "unit": "psi",
      "confidence": 0.94,
      "status": "confirmed|inferred|needs_review",
      "evidence": [
        {
          "source_id": "source_1",
          "location": "page 3",
          "quote": "150 PSI"
        }
      ]
    }
  },
  "missing_attributes": [
    {
      "name": "temperature_range",
      "label": "Temperature Range",
      "reason": "Not found in available sources"
    }
  ],
  "conflicts": [
    {
      "attribute": "pressure_rating",
      "status": "needs_verification",
      "values": [
        {
          "source_id": "source_1",
          "value": 150
        },
        {
          "source_id": "source_2",
          "value": 120
        }
      ]
    }
  ],
  "notes": ["string"]
}
```

## Dynamic Attribute Model

The schema section should be generated for each product type.

Example:

- Pen: ink color, tip size, ink type, body material, refillable
- SSD: capacity, interface, form factor, read speed, write speed, NAND type
- Industrial valve: valve type, pressure rating, connection type, material, temperature range

This is the main difference between product intelligence and a basic fixed-form extractor.

## Evidence Representation

Evidence should show where a value came from.

For the MVP, evidence may include:

- source id
- filename
- page number if available
- short supporting quote

## Confidence Representation

Confidence should be a number between 0 and 1.

Suggested meaning:

- 0.90 to 1.00: very strong evidence and agreement
- 0.70 to 0.89: likely correct but should be reviewed
- below 0.70: weak or uncertain

## Conflict Representation

When multiple sources disagree, the output should not hide the problem.

A conflict object should include:

- attribute name
- values from each source
- conflict status
- verification note

## Missing Attribute Representation

Missing attributes should appear explicitly so the demo shows completeness awareness.

Each missing attribute should include:

- attribute name
- human-readable label
- optional reason

## Notes on Enrichment

If the system suggests a likely value, it should be marked as inferred or suggested, not confirmed.

## MVP Rule

The schema should be strict enough for reliable JSON output but flexible enough to change by product type.
