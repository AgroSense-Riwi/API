"""AgroSense Café — S3 Medallion Structuring, Parquet Snappy & Glue Catalog (AGRO-14 / SCRUM-33).

Implements:
- SCRUM-141: S3 Data Lake folder layout (`raw/`, `trusted/`, `refined/`) and Glacier
  lifecycle transition policies (`GLACIER_IR` at 30d, `GLACIER` at 90d, expire at 365d).
- SCRUM-142: Raw-to-Trusted ingestion converter to columnar Apache Parquet with Snappy
  compression and Hive-style date partitioning (`dt=YYYY-MM-DD`).
- SCRUM-143: AWS Glue Data Catalog / Hive Metastore schema definitions and partition
  registration by date (`dt`).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


@dataclass(frozen=True, slots=True)
class MedallionLayerLayout:
    """Canonical S3 Medallion folder layout & Lifecycle specification (SCRUM-141)."""

    bucket_name: str = "datalakeagrosense"

    RAW_PREFIXES: tuple[str, ...] = (
        "raw/iot_telemetry/",
        "raw/weather_nasa_power/",
        "raw/weather_open_meteo/",
        "raw/soil_lab_reports/",
        "raw/kaggle_crop_reference/",
    )

    TRUSTED_PREFIXES: tuple[str, ...] = (
        "trusted/iot_readings_validated/",
        "trusted/agroclimatic_daily_features/",
        "trusted/soil_analyses_normalized/",
    )

    REFINED_PREFIXES: tuple[str, ...] = (
        "refined/dim_date/",
        "refined/dim_farm/",
        "refined/dim_sensor/",
        "refined/dim_buyer/",
        "refined/dim_alert_type/",
        "refined/fact_alerts_and_visits/",
        "refined/fact_sensor_vs_satellite_climate/",
        "refined/fact_batch_sustainability/",
        "refined/fact_sensor_network_health_daily/",
    )

    def all_prefixes(self) -> tuple[str, ...]:
        return (*self.RAW_PREFIXES, *self.TRUSTED_PREFIXES, *self.REFINED_PREFIXES)

    @staticmethod
    def build_lifecycle_configuration() -> dict[str, Any]:
        """Build S3 Lifecycle Rules transitioning Raw and Trusted layers to Glacier (SCRUM-141)."""
        return {
            "Rules": [
                {
                    "ID": "AgroSense-Raw-To-Glacier-Lifecycle",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "raw/"},
                    "Transitions": [
                        {"Days": 30, "StorageClass": "GLACIER_IR"},
                        {"Days": 120, "StorageClass": "GLACIER"},
                    ],
                    "Expiration": {"Days": 365},
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
                },
                {
                    "ID": "AgroSense-Trusted-To-GlacierIR-Lifecycle",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "trusted/"},
                    "Transitions": [
                        {"Days": 180, "StorageClass": "GLACIER_IR"},
                    ],
                },
            ]
        }

    def provision_s3_medallion_layout(self, s3_client: Any) -> dict[str, Any]:
        """Idempotently provision SSE-S3, prefixes, and Glacier lifecycle on S3 (SCRUM-141)."""
        s3_client.put_bucket_encryption(
            Bucket=self.bucket_name,
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256",
                        }
                    }
                ]
            },
        )
        s3_client.put_public_access_block(
            Bucket=self.bucket_name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )

        for prefix in self.all_prefixes():
            s3_client.put_object(Bucket=self.bucket_name, Key=prefix, Body=b"")

        lifecycle_cfg = self.build_lifecycle_configuration()
        s3_client.put_bucket_lifecycle_configuration(
            Bucket=self.bucket_name,
            LifecycleConfiguration=lifecycle_cfg,
        )

        return {
            "bucket": self.bucket_name,
            "prefixes_created": len(self.all_prefixes()),
            "lifecycle_rules": [r["ID"] for r in lifecycle_cfg["Rules"]],
        }


class RawToTrustedParquetConverter:
    """Converts Raw Bronze JSON/CSV records into Snappy-compressed Trusted Parquet (SCRUM-142)."""

    IOT_TRUSTED_ARROW_SCHEMA = pa.schema(
        [
            pa.field("reading_hash_id", pa.string(), nullable=False),
            pa.field("sensor_id", pa.string(), nullable=False),
            pa.field("farm_id", pa.string(), nullable=False),
            pa.field("sensor_type", pa.string(), nullable=False),
            pa.field("corrected_timestamp", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("expected_slot_ts", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("deviation_minutes", pa.float64(), nullable=False),
            pa.field("is_within_15m_window", pa.bool_(), nullable=False),
            pa.field("is_sensor_stuck_24h", pa.bool_(), nullable=False),
            pa.field("is_valid_for_alerts", pa.bool_(), nullable=False),
            pa.field("standardized_value", pa.float64(), nullable=False),
            pa.field("canonical_unit", pa.string(), nullable=False),
            pa.field("partition_date", pa.string(), nullable=False),
        ]
    )

    CANONICAL_UNITS: dict[str, str] = {
        "SOIL_MOISTURE": "%",
        "TEMPERATURE": "CELSIUS",
        "PLUVIOMETRY": "MM",
        "PLUVIOMETER": "MM",
    }

    @classmethod
    def normalize_iot_record(cls, raw: dict[str, Any]) -> dict[str, Any]:
        """Transform a Raw Bronze IoT reading into the Silver `iot_readings_validated` schema."""
        sensor_id = str(raw["sensor_id"]).strip()
        farm_id = str(raw["farm_id"]).strip()
        raw_type = str(raw.get("sensor_type") or raw.get("metric") or "SOIL_MOISTURE").upper()
        sensor_type = "PLUVIOMETRY" if raw_type == "PLUVIOMETER" else raw_type

        raw_ts = (
            raw.get("device_timestamp_raw")
            or raw.get("reading_timestamp")
            or raw.get("observed_at")
        )
        if isinstance(raw_ts, datetime):
            ts = raw_ts
        else:
            ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        ts = ts.astimezone(UTC)

        drift_sec = int(raw.get("clock_drift_seconds", 0))
        corrected_ts = ts - timedelta(seconds=drift_sec)

        # Snap to nearest 10-minute expected slot (RN-01)
        minute_mod = corrected_ts.minute % 10
        slot_base = corrected_ts.replace(
            minute=corrected_ts.minute - minute_mod, second=0, microsecond=0
        )
        if minute_mod >= 5:
            expected_slot_ts = slot_base + timedelta(minutes=10)
        else:
            expected_slot_ts = slot_base

        deviation_minutes = round(abs((corrected_ts - expected_slot_ts).total_seconds()) / 60.0, 4)
        is_within_15m = deviation_minutes <= 15.0
        is_stuck_24h = bool(raw.get("is_sensor_stuck_24h", False))
        is_valid = is_within_15m and not is_stuck_24h

        raw_val = float(raw.get("raw_value", raw.get("reading_value", raw.get("value", 0.0))))
        canonical_unit = cls.CANONICAL_UNITS.get(sensor_type, str(raw.get("raw_unit", "%")))
        partition_date = corrected_ts.strftime("%Y-%m-%d")

        hash_input = f"{sensor_id}|{farm_id}|{sensor_type}|{corrected_ts.isoformat()}"
        reading_hash_id = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:32]

        return {
            "reading_hash_id": reading_hash_id,
            "sensor_id": sensor_id,
            "farm_id": farm_id,
            "sensor_type": sensor_type,
            "corrected_timestamp": corrected_ts,
            "expected_slot_ts": expected_slot_ts,
            "deviation_minutes": deviation_minutes,
            "is_within_15m_window": is_within_15m,
            "is_sensor_stuck_24h": is_stuck_24h,
            "is_valid_for_alerts": is_valid,
            "standardized_value": round(raw_val, 4),
            "canonical_unit": canonical_unit,
            "partition_date": partition_date,
        }

    @classmethod
    def records_to_snappy_parquet_bytes(cls, normalized_records: list[dict[str, Any]]) -> bytes:
        """Serialize normalized records into an in-memory Apache Parquet buffer with Snappy."""
        table = pa.Table.from_pylist(normalized_records, schema=cls.IOT_TRUSTED_ARROW_SCHEMA)
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="snappy", use_dictionary=True)
        return buf.getvalue()

    @classmethod
    def convert_and_partition_raw_payload(
        cls,
        raw_payload: list[dict[str, Any]] | dict[str, Any] | str | bytes,
    ) -> dict[str, bytes]:
        """Convert raw JSON payload into Snappy Parquet buffers keyed by `dt=YYYY-MM-DD`."""
        if isinstance(raw_payload, (str, bytes)):
            parsed = json.loads(raw_payload)
        else:
            parsed = raw_payload

        if isinstance(parsed, dict) and "readings" in parsed:
            items = parsed["readings"]
        elif isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict):
            items = [parsed]
        else:
            raise ValueError("Unsupported raw telemetry payload structure")

        by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        seen_hashes: set[str] = set()

        for item in items:
            norm = cls.normalize_iot_record(item)
            if norm["reading_hash_id"] in seen_hashes:
                continue
            seen_hashes.add(norm["reading_hash_id"])
            by_date[norm["partition_date"]].append(norm)

        parquet_partitions: dict[str, bytes] = {}
        for dt_str, records in sorted(by_date.items()):
            ordered = sorted(records, key=lambda r: (r["sensor_id"], r["corrected_timestamp"]))
            parquet_partitions[dt_str] = cls.records_to_snappy_parquet_bytes(ordered)

        return parquet_partitions

    @classmethod
    def build_trusted_s3_key(
        cls,
        dataset_name: str,
        partition_date: str,
        part_name: str = "part-00000.snappy.parquet",
    ) -> str:
        """Build Hive-partitioned S3 key in the Trusted (Silver) layer (`dt=YYYY-MM-DD`)."""
        return f"trusted/{dataset_name}/dt={partition_date}/{part_name}"

    @classmethod
    def upload_trusted_partitions_to_s3(
        cls,
        s3_client: Any,
        bucket_name: str,
        raw_payload: list[dict[str, Any]] | dict[str, Any] | str | bytes,
        dataset_name: str = "iot_readings_validated",
    ) -> list[str]:
        """Convert raw payload to Snappy Parquet and upload partitioned objects to S3."""
        partitions = cls.convert_and_partition_raw_payload(raw_payload)
        uploaded_uris: list[str] = []
        for dt_str, parquet_bytes in partitions.items():
            s3_key = cls.build_trusted_s3_key(dataset_name, dt_str)
            s3_client.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=parquet_bytes,
                ContentType="application/vnd.apache.parquet",
            )
            uploaded_uris.append(f"s3://{bucket_name}/{s3_key}")
        return uploaded_uris


class GlueDataCatalogManager:
    """Manages AWS Glue Data Catalog / Hive Metastore tables & date partitions (SCRUM-143)."""

    PARQUET_INPUT_FORMAT = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    PARQUET_OUTPUT_FORMAT = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    PARQUET_SERDE = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"

    def __init__(
        self,
        database_name: str = "agrosense_datalake",
        bucket_name: str = "datalakeagrosense",
    ) -> None:
        self.database_name = database_name
        self.bucket_name = bucket_name

    def get_catalog_tables_spec(self) -> dict[str, dict[str, Any]]:
        """Return Glue TableInput specifications for Trusted (Silver) and Refined (Gold) layers."""
        return {
            "trusted_iot_readings_validated": {
                "prefix": "trusted/iot_readings_validated/",
                "columns": [
                    {"Name": "reading_hash_id", "Type": "string"},
                    {"Name": "sensor_id", "Type": "string"},
                    {"Name": "farm_id", "Type": "string"},
                    {"Name": "sensor_type", "Type": "string"},
                    {"Name": "corrected_timestamp", "Type": "timestamp"},
                    {"Name": "expected_slot_ts", "Type": "timestamp"},
                    {"Name": "deviation_minutes", "Type": "double"},
                    {"Name": "is_within_15m_window", "Type": "boolean"},
                    {"Name": "is_sensor_stuck_24h", "Type": "boolean"},
                    {"Name": "is_valid_for_alerts", "Type": "boolean"},
                    {"Name": "standardized_value", "Type": "double"},
                    {"Name": "canonical_unit", "Type": "string"},
                    {"Name": "partition_date", "Type": "string"},
                ],
                "partition_keys": [{"Name": "dt", "Type": "string"}],
            },
            "trusted_agroclimatic_daily_features": {
                "prefix": "trusted/agroclimatic_daily_features/",
                "columns": [
                    {"Name": "farm_id", "Type": "string"},
                    {"Name": "observation_date", "Type": "date"},
                    {"Name": "high_humidity_hours_gt85", "Type": "int"},
                    {"Name": "daily_mean_temp_c", "Type": "double"},
                    {"Name": "soil_moisture_mean_pct", "Type": "double"},
                    {"Name": "precip_accum_72h_mm", "Type": "double"},
                    {"Name": "forecast_rain_next_5d_mm", "Type": "double"},
                    {"Name": "consecutive_rust_days", "Type": "int"},
                    {"Name": "consecutive_dry_soil_days", "Type": "int"},
                    {"Name": "primary_source_used", "Type": "string"},
                ],
                "partition_keys": [{"Name": "dt", "Type": "string"}],
            },
            "trusted_soil_analyses_normalized": {
                "prefix": "trusted/soil_analyses_normalized/",
                "columns": [
                    {"Name": "analysis_id", "Type": "string"},
                    {"Name": "farm_id", "Type": "string"},
                    {"Name": "sample_date", "Type": "date"},
                    {"Name": "original_unit", "Type": "string"},
                    {"Name": "conversion_factor_applied", "Type": "double"},
                    {"Name": "n_mg_kg", "Type": "double"},
                    {"Name": "p_mg_kg", "Type": "double"},
                    {"Name": "k_mg_kg", "Type": "double"},
                    {"Name": "ph", "Type": "double"},
                ],
                "partition_keys": [{"Name": "dt", "Type": "string"}],
            },
            "refined_fact_alerts_and_visits": {
                "prefix": "refined/fact_alerts_and_visits/",
                "columns": [
                    {"Name": "fact_alert_visit_id", "Type": "bigint"},
                    {"Name": "date_key", "Type": "int"},
                    {"Name": "farm_key", "Type": "int"},
                    {"Name": "alert_type_key", "Type": "int"},
                    {"Name": "priority_score", "Type": "int"},
                    {"Name": "was_attended", "Type": "boolean"},
                    {"Name": "visit_outcome", "Type": "string"},
                    {"Name": "days_to_attend", "Type": "int"},
                    {"Name": "harvest_yield_kg_ha", "Type": "double"},
                    {"Name": "estimated_loss_prevented_pct", "Type": "double"},
                ],
                "partition_keys": [{"Name": "dt", "Type": "string"}],
            },
            "refined_fact_batch_sustainability": {
                "prefix": "refined/fact_batch_sustainability/",
                "columns": [
                    {"Name": "fact_sustainability_id", "Type": "bigint"},
                    {"Name": "date_key", "Type": "int"},
                    {"Name": "buyer_key", "Type": "int"},
                    {"Name": "farm_key", "Type": "int"},
                    {"Name": "batch_code", "Type": "string"},
                    {"Name": "contributed_weight_kg", "Type": "double"},
                    {"Name": "batch_share_pct", "Type": "double"},
                    {"Name": "weighted_water_m3_ton", "Type": "double"},
                    {"Name": "fertilizer_reduction_pct", "Type": "double"},
                    {"Name": "sca_cupping_score", "Type": "double"},
                    {"Name": "is_zero_deforestation_compliant", "Type": "boolean"},
                ],
                "partition_keys": [{"Name": "dt", "Type": "string"}],
            },
        }

    def build_glue_table_input(self, table_name: str) -> dict[str, Any]:
        """Build AWS Glue `TableInput` payload with Parquet + Snappy SerDe and `dt` partition."""
        spec = self.get_catalog_tables_spec()[table_name]
        s3_location = f"s3://{self.bucket_name}/{spec['prefix']}"
        return {
            "Name": table_name,
            "TableType": "EXTERNAL_TABLE",
            "Parameters": {
                "EXTERNAL": "TRUE",
                "classification": "parquet",
                "parquet.compression": "SNAPPY",
                "projection.enabled": "true",
                "projection.dt.type": "date",
                "projection.dt.format": "yyyy-MM-dd",
                "projection.dt.range": "2024-01-01,NOW",
                "storage.location.template": f"{s3_location}dt=${{dt}}/",
            },
            "StorageDescriptor": {
                "Columns": spec["columns"],
                "Location": s3_location,
                "InputFormat": self.PARQUET_INPUT_FORMAT,
                "OutputFormat": self.PARQUET_OUTPUT_FORMAT,
                "Compressed": True,
                "SerdeInfo": {
                    "SerializationLibrary": self.PARQUET_SERDE,
                    "Parameters": {"serialization.format": "1"},
                },
            },
            "PartitionKeys": spec["partition_keys"],
        }

    def generate_hive_ddl(self, table_name: str) -> str:
        """Generate Hive / Amazon Athena `CREATE EXTERNAL TABLE` DDL statement (SCRUM-143)."""
        spec = self.get_catalog_tables_spec()[table_name]
        cols_ddl = ",\n  ".join(f"{c['Name']} {c['Type'].upper()}" for c in spec["columns"])
        part_ddl = ", ".join(f"{p['Name']} {p['Type'].upper()}" for p in spec["partition_keys"])
        location = f"s3://{self.bucket_name}/{spec['prefix']}"
        return (
            f"CREATE EXTERNAL TABLE IF NOT EXISTS {self.database_name}.{table_name} (\n"
            f"  {cols_ddl}\n"
            f")\n"
            f"PARTITIONED BY ({part_ddl})\n"
            f"STORED AS PARQUET\n"
            f"LOCATION '{location}'\n"
            f"TBLPROPERTIES ('parquet.compression'='SNAPPY', 'classification'='parquet');"
        )

    def sync_glue_catalog(self, glue_client: Any) -> list[str]:
        """Idempotently create Glue Database and register all Medallion Parquet tables."""
        with contextlib.suppress(Exception):
            glue_client.create_database(
                DatabaseInput={
                    "Name": self.database_name,
                    "Description": "AgroSense Cafe S3 Medallion Data Lake Catalog (AGRO-14)",
                }
            )

        synced_tables: list[str] = []
        for table_name in self.get_catalog_tables_spec():
            table_input = self.build_glue_table_input(table_name)
            try:
                glue_client.create_table(
                    DatabaseName=self.database_name,
                    TableInput=table_input,
                )
            except Exception:
                glue_client.update_table(
                    DatabaseName=self.database_name,
                    TableInput=table_input,
                )
            synced_tables.append(table_name)
        return synced_tables

    def register_partition(
        self,
        glue_client: Any,
        table_name: str,
        partition_date: str,
    ) -> dict[str, Any]:
        """Idempotently register a `dt=YYYY-MM-DD` partition in AWS Glue Data Catalog."""
        table_input = self.build_glue_table_input(table_name)
        sd = dict(table_input["StorageDescriptor"])
        sd["Location"] = f"{sd['Location']}dt={partition_date}/"
        partition_input = {
            "Values": [partition_date],
            "StorageDescriptor": sd,
            "Parameters": {"parquet.compression": "SNAPPY"},
        }
        try:
            glue_client.create_partition(
                DatabaseName=self.database_name,
                TableName=table_name,
                PartitionInput=partition_input,
            )
        except Exception:
            glue_client.update_partition(
                DatabaseName=self.database_name,
                TableName=table_name,
                PartitionValueList=[partition_date],
                PartitionInput=partition_input,
            )
        return partition_input
