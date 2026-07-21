# HDB Resale Flat Price ETL Pipeline

## Overview
Processes HDB (Housing and Development Board) resale flat price data from January 2012 to December 2016. Produces cleaned, transformed, failed, and hashed datasets.

## Setup

### Prerequisites
- Python 3.8+
- Jupyter Notebook

### Installation
```bash
pip install -r requirements.txt
```

## Running the Pipeline

1. Place raw HDB resale CSV files in `data/raw/`
2. Open `etl_pipeline.ipynb` in Jupyter Notebook
3. Run all cells
4. Outputs will be written to `data/processed/`

## Outputs

The pipeline generates four datasets:

- **cleaned.csv** — Validated records with data quality fixes applied
- **transformed.csv** — Cleaned data with business logic and derived features
- **failed.csv** — Records that failed validation (includes error details)
- **hashed.csv** — Transformed data with PII/sensitive columns hashed

## Data Dictionary

See `data/README.md` for details on input/output columns and transformations.

## Project Structure

```
.
├── etl_pipeline.ipynb     # Main ETL notebook
├── config.py              # Configuration and schemas
├── requirements.txt       # Python dependencies
├── README.md              # This file
└── data/
    ├── raw/               # Input CSV files
    └── processed/         # Output datasets
```
