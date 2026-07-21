import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"

EXPECTED_COLUMNS = [
    "month",
    "town",
    "flat_type",
    "block",
    "street_name",
    "storey_range",
    "floor_area_sqm",
    "flat_model",
    "lease_commence_date",
    "resale_price"
]

NUMERIC_COLUMNS = ["floor_area_sqm", "lease_commence_date", "resale_price"]
CATEGORICAL_COLUMNS = ["town", "flat_type", "storey_range", "flat_model"]

VALIDATION_RULES = {
    "resale_price": {"min": 0, "max": 2000000},
    "floor_area_sqm": {"min": 10, "max": 200},
    "lease_commence_date": {"min": 1966, "max": 2016}
}

PII_COLUMNS_TO_HASH = []
