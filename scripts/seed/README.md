# Soil analysis seed flow

The seed generates raw F6 reports in `soil_lab_reports`, injects
`DEFECT_MIXED_UNITS` at the configured `--defect-rate`, and produces canonical
`soil_analyses` rows with nutrient values in mg/kg. The original measurements,
reported units, and quality-defect labels are retained in `source_data`.

Supported soil unit conversions are ppm and mg/kg (numerically equivalent for
these records), g/kg, percentage, and potassium cmol/kg. Unsupported units stop
normalization with an error rather than being silently treated as mg/kg.
`soil_analyses` is the same table name and operational field shape used by the
API migration/model.

## Optional F1 reference comparison

The F1 CSV can be loaded from S3 using the local AWS profile, role, or environment
credentials. Set the region with `AWS_DEFAULT_REGION` if the bucket is not in the
SDK default region. Never place credentials in seed arguments or source files.
The AgroSense F1 dictionary maps N/P/K to mg/kg (ppm is numerically equivalent),
so `mg/kg` is the default comparison unit:

```powershell
uv run --group dev python -m scripts.seed --soil-reference-s3-uri "s3://data-lake-agrosense/bronze/kaggle_crop_reference/year=2026/month=10/day=14/Crop_recommendation.csv"
```

The CSV must have the Kaggle recommendation columns `N`, `P`, `K`, `ph`, and
`label`.
The seed summarizes the `coffee` rows and reports whether each normalized F6
nutrient and pH lie within that crop's observed F1 range. The source dataset's
N/P/K unit can be overridden with `--f1-npk-unit` if a source version differs;
comparison is rejected for other units because no conversion from units such as
kg/ha is inferred.
This descriptive range comparison is not a fertilizer prescription and does
not substantiate a 20% fertilizer reduction. F1 measurements and agronomic
recommendations require validation by a qualified agronomist.

Without `--soil-reference-s3-uri`, seeding and unit normalization run locally
without AWS access or a database connection. The seed continues to generate
PostgreSQL UPSERT statements and verify idempotency in its in-memory store.
