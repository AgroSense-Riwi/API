"""AgroSense Café — Gold Star Schema built with Apache PySpark (SCRUM-184 / CT-07).

Reads the operational PostgreSQL tables, builds the dimensional model defined in
``agrosense_api.services.star_schema_gold`` (``dim_finca``, ``dim_tiempo``,
``fact_entregas``, ``fact_alertas``) with a Spark DataFrame pipeline and writes it as
Snappy Parquet to ``s3://<bucket>/gold/<table>/``, the locations registered in Athena
(``ATHENA_GOLD_STAR_SCHEMA_DDL``) and read by Power BI.

The module is self-contained (only ``pyspark``, ``psycopg`` and ``boto3``) so it runs
inside the Airflow image without installing the API package.

Usage::

    DATABASE_URL=postgresql://... S3_BUCKET=datalakeagrosense \
        python pipelines/spark_jobs/gold_star_schema_pyspark.py
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GOLD_PREFIX = "gold"
GOLD_TABLES = ("dim_finca", "dim_tiempo", "fact_entregas", "fact_alertas")

# Operator role: export_batches has FORCE ROW LEVEL SECURITY (RN-11); the analytical
# extraction reads every batch, like the operator endpoints of the API.
SET_OPERATOR_ROLE = "SELECT set_config('app.current_role', 'operator', false)"

FARMS_QUERY = """
SELECT
    f.farm_id::text            AS farm_id,
    f.farm_code                AS farm_code,
    p.full_name                AS farm_name,
    m.name                     AS municipality_name,
    f.cultivated_area_ha::float8 AS total_area_hectares,
    f.latitude::float8         AS latitude,
    f.longitude::float8        AS longitude
FROM farms f
JOIN producers p ON p.producer_id = f.producer_id
JOIN municipalities m ON m.municipality_id = f.municipality_id
ORDER BY f.farm_code
"""

# One row per delivery. If a delivery was split across several batches, the batch with
# the largest contribution is kept (fact grain = delivery).
DELIVERIES_QUERY = """
SELECT DISTINCT ON (d.delivery_id)
    d.delivery_id::text           AS delivery_id,
    d.delivery_date               AS delivery_date,
    d.farm_id::text               AS farm_id,
    fp.variety_id                 AS variety_id,
    d.delivered_weight_kg::float8 AS delivered_weight_kg,
    c.batch_id::text              AS batch_id,
    b.batch_code                  AS batch_code
FROM harvest_deliveries d
LEFT JOIN farm_plots fp ON fp.plot_id = d.plot_id
LEFT JOIN batch_farm_compositions c ON c.delivery_id = d.delivery_id
LEFT JOIN export_batches b ON b.batch_id = c.batch_id
ORDER BY d.delivery_id, c.contributed_kg DESC NULLS LAST
"""

ALERTS_QUERY = """
SELECT
    a.alert_id::text                       AS alert_id,
    CAST(TO_CHAR(a.alert_date, 'YYYYMMDD') AS INT) AS date_key,
    a.farm_id::text                        AS farm_id,
    a.alert_type                           AS alert_type,
    a.severity                             AS severity,
    a.alert_status                         AS alert_status,
    a.soil_moisture_mean_pct::float8       AS soil_moisture_mean_pct,
    a.precip_accum_72h_mm::float8          AS precip_accum_72h_mm
FROM agronomic_alerts a
ORDER BY a.alert_date DESC, a.alert_id
"""


def to_libpq_dsn(database_url: str) -> str:
    """Convert a SQLAlchemy URL (``postgresql+psycopg://``) into a libpq DSN."""
    for driver in ("+psycopg2", "+psycopg", "+asyncpg"):
        database_url = database_url.replace(driver, "")
    return database_url


def extract_operational_rows(
    database_url: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read farms and deliveries from PostgreSQL as lists of dicts."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(to_libpq_dsn(database_url), row_factory=dict_row) as conn:
        conn.execute(SET_OPERATOR_ROLE)
        farms = conn.execute(FARMS_QUERY).fetchall()
        deliveries = conn.execute(DELIVERIES_QUERY).fetchall()
    logger.info("Extracted %d farms and %d deliveries", len(farms), len(deliveries))
    return farms, deliveries


def extract_operational_alerts(database_url: str) -> list[dict[str, Any]]:
    """Read agronomic_alerts from PostgreSQL if the table exists and has rows."""
    import psycopg
    from psycopg.rows import dict_row

    try:
        with psycopg.connect(to_libpq_dsn(database_url), row_factory=dict_row) as conn:
            conn.execute(SET_OPERATOR_ROLE)
            exists = conn.execute(
                "SELECT to_regclass('public.agronomic_alerts') IS NOT NULL AS ok"
            ).fetchone()
            if not exists or not exists["ok"]:
                return []
            alerts = conn.execute(ALERTS_QUERY).fetchall()
        logger.info("Extracted %d agronomic alerts", len(alerts))
        return alerts
    except Exception as exc:
        logger.warning("Could not extract agronomic_alerts: %s", exc)
        return []


# ==============================================================
# Spark schemas (same columns and types as ATHENA_GOLD_STAR_SCHEMA_DDL)
# ==============================================================


def gold_schemas() -> dict[str, Any]:
    from pyspark.sql.types import (
        DateType,
        DoubleType,
        IntegerType,
        StringType,
        StructField,
        StructType,
    )

    return {
        "farms_source": StructType(
            [
                StructField("farm_id", StringType(), False),
                StructField("farm_code", StringType(), False),
                StructField("farm_name", StringType(), True),
                StructField("municipality_name", StringType(), True),
                StructField("total_area_hectares", DoubleType(), True),
                StructField("latitude", DoubleType(), True),
                StructField("longitude", DoubleType(), True),
            ]
        ),
        "deliveries_source": StructType(
            [
                StructField("delivery_id", StringType(), False),
                StructField("delivery_date", DateType(), False),
                StructField("farm_id", StringType(), True),
                StructField("variety_id", IntegerType(), True),
                StructField("delivered_weight_kg", DoubleType(), False),
                StructField("batch_id", StringType(), True),
                StructField("batch_code", StringType(), True),
            ]
        ),
        "fact_alertas": StructType(
            [
                StructField("alert_id", StringType(), False),
                StructField("date_key", IntegerType(), False),
                StructField("farm_id", StringType(), False),
                StructField("alert_type", StringType(), False),
                StructField("severity", StringType(), False),
                StructField("alert_status", StringType(), False),
                StructField("soil_moisture_mean_pct", DoubleType(), True),
                StructField("precip_accum_72h_mm", DoubleType(), True),
            ]
        ),
    }


def build_spark_session(app_name: str = "agrosense-gold-star-schema") -> Any:
    from pyspark.sql import SparkSession

    return (
        SparkSession.builder.appName(app_name)
        .master(os.getenv("SPARK_MASTER", "local[*]"))
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", os.getenv("SPARK_SHUFFLE_PARTITIONS", "4"))
        .config("spark.sql.parquet.compression.codec", "snappy")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )


def _normalize_alerts(alerts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not alerts:
        return []
    normalized: list[dict[str, Any]] = []
    for item in alerts:
        dk = item.get("date_key")
        if dk is None and item.get("alert_date") is not None:
            ad = item["alert_date"]
            if isinstance(ad, date):
                dk = int(ad.strftime("%Y%m%d"))
            else:
                dk = int(str(ad).replace("-", "")[:8])
        normalized.append(
            {
                "alert_id": str(item["alert_id"]),
                "date_key": int(dk or 20260101),
                "farm_id": str(item["farm_id"]),
                "alert_type": str(item["alert_type"]),
                "severity": str(item["severity"]),
                "alert_status": str(item.get("alert_status") or "OPEN"),
                "soil_moisture_mean_pct": (
                    float(item["soil_moisture_mean_pct"])
                    if item.get("soil_moisture_mean_pct") is not None
                    else None
                ),
                "precip_accum_72h_mm": (
                    float(item["precip_accum_72h_mm"])
                    if item.get("precip_accum_72h_mm") is not None
                    else None
                ),
            }
        )
    return normalized


def build_gold_tables(
    spark: Any,
    farms: list[dict[str, Any]],
    deliveries: list[dict[str, Any]],
    calendar_start: date | None = None,
    calendar_end: date | None = None,
    alerts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the four gold DataFrames from the operational rows."""
    from pyspark.sql import functions as F

    schemas = gold_schemas()
    farms_df = spark.createDataFrame(farms, schema=schemas["farms_source"])
    deliveries_df = spark.createDataFrame(deliveries, schema=schemas["deliveries_source"])

    dim_finca = farms_df.select(
        "farm_id",
        "farm_code",
        "farm_name",
        "municipality_name",
        F.lit(None).cast("double").alias("altitude_meters"),
        "total_area_hectares",
        "latitude",
        "longitude",
    )

    date_key = F.date_format("delivery_date", "yyyyMMdd").cast("int")
    fact_entregas = deliveries_df.select(
        "delivery_id",
        date_key.alias("date_key"),
        "farm_id",
        "variety_id",
        "delivered_weight_kg",
        F.lit(None).cast("boolean").alias("is_organic"),
        "batch_id",
        "batch_code",
    )

    # Calendar: whole years covering every delivery (or the current year if empty).
    if calendar_start is None or calendar_end is None:
        bounds = deliveries_df.agg(
            F.min("delivery_date").alias("lo"), F.max("delivery_date").alias("hi")
        ).first()
        today = date.today()
        lo = bounds["lo"] or today
        hi = bounds["hi"] or today
        calendar_start = calendar_start or date(lo.year, 1, 1)
        calendar_end = calendar_end or date(hi.year, 12, 31)

    days = (calendar_end - calendar_start).days
    calendar = spark.range(0, days + 1).select(
        F.date_add(F.lit(calendar_start), F.col("id").cast("int")).alias("d")
    )
    dim_tiempo = calendar.select(
        F.date_format("d", "yyyyMMdd").cast("int").alias("date_key"),
        F.date_format("d", "yyyy-MM-dd").alias("full_date"),
        F.year("d").alias("year"),
        F.month("d").alias("month"),
        F.dayofmonth("d").alias("day"),
        F.quarter("d").alias("quarter"),
        # ISO weekday, Monday = 1 ... Sunday = 7 (same as StarSchemaGoldPipeline).
        ((F.dayofweek("d") + 5) % 7 + 1).alias("day_of_week"),
        F.dayofweek("d").isin(1, 7).alias("is_weekend"),
    )

    normalized_alerts = _normalize_alerts(alerts)
    fact_alertas = spark.createDataFrame(normalized_alerts, schema=schemas["fact_alertas"])

    return {
        "dim_finca": dim_finca,
        "dim_tiempo": dim_tiempo,
        "fact_entregas": fact_entregas,
        "fact_alertas": fact_alertas,
    }


def write_gold_tables(tables: dict[str, Any], output_dir: str | Path) -> dict[str, Path]:
    """Write each DataFrame as Snappy Parquet under ``output_dir/<table>/``."""
    paths: dict[str, Path] = {}
    for name, df in tables.items():
        target = Path(output_dir) / name
        df.coalesce(1).write.mode("overwrite").option("compression", "snappy").parquet(str(target))
        paths[name] = target
    return paths


def upload_gold_tables(
    s3_client: Any, bucket: str, local_paths: dict[str, Path], prefix: str = GOLD_PREFIX
) -> dict[str, list[str]]:
    """Replace ``s3://bucket/prefix/<table>/`` with the Parquet files of each table.

    Objects already under the table prefix are deleted first so a rerun leaves exactly
    one version of the table (idempotent, CT-08). Spark's ``_SUCCESS`` and ``.crc``
    files are not uploaded.
    """
    uploaded: dict[str, list[str]] = {}
    for name, local_dir in local_paths.items():
        table_prefix = f"{prefix}/{name}/"
        paginator = s3_client.get_paginator("list_objects_v2")
        stale = [
            {"Key": obj["Key"]}
            for page in paginator.paginate(Bucket=bucket, Prefix=table_prefix)
            for obj in page.get("Contents", [])
        ]
        for i in range(0, len(stale), 1000):
            s3_client.delete_objects(Bucket=bucket, Delete={"Objects": stale[i : i + 1000]})

        uploaded[name] = []
        for part in sorted(Path(local_dir).glob("*.parquet")):
            key = f"{table_prefix}{part.name}"
            s3_client.upload_file(
                str(part), bucket, key, ExtraArgs={"ContentType": "application/x-parquet"}
            )
            uploaded[name].append(f"s3://{bucket}/{key}")
    return uploaded


def run_gold_star_schema_job(
    database_url: str,
    bucket: str,
    s3_client: Any | None = None,
    spark: Any | None = None,
) -> dict[str, Any]:
    """Extract from PostgreSQL, build the star schema with Spark and publish it to S3."""
    farms, deliveries = extract_operational_rows(database_url)
    alerts = extract_operational_alerts(database_url)

    own_session = spark is None
    spark = spark or build_spark_session()
    try:
        tables = build_gold_tables(spark, farms, deliveries, alerts=alerts)
        counts = {name: df.count() for name, df in tables.items()}
        with tempfile.TemporaryDirectory(prefix="agrosense-gold-") as tmp:
            paths = write_gold_tables(tables, tmp)
            if s3_client is None:
                import boto3

                s3_client = boto3.client("s3")
            uris = upload_gold_tables(s3_client, bucket, paths)
    finally:
        if own_session:
            spark.stop()

    logger.info("Gold star schema published to s3://%s/%s/: %s", bucket, GOLD_PREFIX, counts)
    return {"status": "SUCCESS", "bucket": bucket, "counts": counts, "uploaded_s3_uris": uris}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    database_url = os.environ["DATABASE_URL"]
    bucket = os.getenv("S3_BUCKET_GOLD") or os.getenv("S3_BUCKET") or "datalakeagrosense"
    result = run_gold_star_schema_job(database_url, bucket)
    for name in GOLD_TABLES:
        logger.info("%-14s rows=%d", name, result["counts"][name])


if __name__ == "__main__":
    main()


__all__ = [
    "ALERTS_QUERY",
    "DELIVERIES_QUERY",
    "FARMS_QUERY",
    "GOLD_TABLES",
    "build_gold_tables",
    "build_spark_session",
    "extract_operational_alerts",
    "extract_operational_rows",
    "run_gold_star_schema_job",
    "to_libpq_dsn",
    "upload_gold_tables",
    "write_gold_tables",
]
