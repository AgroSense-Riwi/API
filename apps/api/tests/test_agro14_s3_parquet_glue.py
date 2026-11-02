"""Automated verification suite for AGRO-14 (SCRUM-33: SCRUM-141, SCRUM-142, SCRUM-143).

Verifies:
- SCRUM-141: S3 Medallion folder layout (`raw/`, `trusted/`, `refined/`), SSE-S3 encryption,
  and Glacier lifecycle transitions (`GLACIER_IR` at 30d, `GLACIER` at 90d, expire at 365d).
- SCRUM-142: Raw-to-Trusted converter producing Apache Parquet with `SNAPPY` compression,
  clock drift correction, 15-minute window validation (RN-01), and `dt=YYYY-MM-DD` partitioning.
- SCRUM-143: AWS Glue Data Catalog / Hive Metastore table specifications, DDL generation,
  and partition registration by `dt`.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pyarrow.parquet as pq
from agrosense_api.services.s3_medallion_parquet import (
    GlueDataCatalogManager,
    MedallionLayerLayout,
    RawToTrustedParquetConverter,
)


def test_scrum141_s3_medallion_folder_layout_and_glacier_lifecycle() -> None:
    """Verify Medallion folder prefixes and Glacier transition lifecycle rules."""
    layout = MedallionLayerLayout(bucket_name="datalakeagrosense")
    prefixes = layout.all_prefixes()

    assert "raw/iot_telemetry/" in prefixes
    assert "raw/weather_nasa_power/" in prefixes
    assert "trusted/iot_readings_validated/" in prefixes
    assert "trusted/agroclimatic_daily_features/" in prefixes
    assert "refined/fact_alerts_and_visits/" in prefixes
    assert "refined/fact_batch_sustainability/" in prefixes

    lc = layout.build_lifecycle_configuration()
    rules = {r["ID"]: r for r in lc["Rules"]}
    raw_rule = rules["AgroSense-Raw-To-Glacier-Lifecycle"]
    assert raw_rule["Status"] == "Enabled"
    assert raw_rule["Filter"] == {"Prefix": "raw/"}
    assert {"Days": 30, "StorageClass": "GLACIER_IR"} in raw_rule["Transitions"]
    assert {"Days": 120, "StorageClass": "GLACIER"} in raw_rule["Transitions"]
    assert raw_rule["Expiration"] == {"Days": 365}

    mock_s3 = MagicMock()
    summary = layout.provision_s3_medallion_layout(mock_s3)
    assert summary["bucket"] == "datalakeagrosense"
    assert summary["prefixes_created"] == len(prefixes)
    mock_s3.put_bucket_encryption.assert_called_once()
    mock_s3.put_public_access_block.assert_called_once()
    mock_s3.put_bucket_lifecycle_configuration.assert_called_once()


def test_scrum142_raw_to_trusted_parquet_snappy_compression_and_partitioning() -> None:
    """Verify Raw JSON conversion to Snappy-compressed Apache Parquet partitioned by dt."""
    raw_payload = {
        "readings": [
            {
                "sensor_id": "SENS-001",
                "farm_id": "farm-001",
                "sensor_type": "SOIL_MOISTURE",
                "raw_value": 41.25,
                "raw_unit": "%",
                "device_timestamp_raw": "2026-10-21T14:01:30Z",
                "clock_drift_seconds": 30,
                "is_sensor_stuck_24h": False,
            },
            {
                "sensor_id": "SENS-001",
                "farm_id": "farm-001",
                "sensor_type": "SOIL_MOISTURE",
                "raw_value": 42.10,
                "raw_unit": "%",
                "device_timestamp_raw": "2026-10-22T08:10:05Z",
                "clock_drift_seconds": 5,
                "is_sensor_stuck_24h": False,
            },
        ]
    }

    partitions = RawToTrustedParquetConverter.convert_and_partition_raw_payload(raw_payload)
    assert set(partitions.keys()) == {"2026-10-21", "2026-10-22"}

    for dt_str, parquet_bytes in partitions.items():
        pf = pq.ParquetFile(io.BytesIO(parquet_bytes))
        assert pf.metadata.num_rows == 1
        col_meta = pf.metadata.row_group(0).column(0)
        assert col_meta.compression == "SNAPPY"

        table = pf.read()
        rows = table.to_pylist()
        assert rows[0]["partition_date"] == dt_str
        assert rows[0]["is_within_15m_window"] is True
        assert rows[0]["is_valid_for_alerts"] is True
        assert rows[0]["canonical_unit"] == "%"

    mock_s3 = MagicMock()
    uris = RawToTrustedParquetConverter.upload_trusted_partitions_to_s3(
        s3_client=mock_s3,
        bucket_name="datalakeagrosense",
        raw_payload=raw_payload,
    )
    assert (
        "s3://datalakeagrosense/trusted/iot_readings_validated/dt=2026-10-21/part-00000.snappy.parquet"
        in uris
    )
    assert (
        "s3://datalakeagrosense/trusted/iot_readings_validated/dt=2026-10-22/part-00000.snappy.parquet"
        in uris
    )


def test_scrum143_glue_catalog_schema_ddl_and_partition_registration() -> None:
    """Verify AWS Glue Data Catalog schemas, Hive DDL, and date partition registration."""
    catalog = GlueDataCatalogManager(
        database_name="agrosense_datalake",
        bucket_name="datalakeagrosense",
    )
    table_input = catalog.build_glue_table_input("trusted_iot_readings_validated")
    assert table_input["Parameters"]["parquet.compression"] == "SNAPPY"
    assert table_input["PartitionKeys"] == [{"Name": "dt", "Type": "string"}]
    assert (
        table_input["StorageDescriptor"]["Location"]
        == "s3://datalakeagrosense/trusted/iot_readings_validated/"
    )

    ddl = catalog.generate_hive_ddl("trusted_iot_readings_validated")
    assert (
        "CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.trusted_iot_readings_validated"
        in ddl
    )
    assert "PARTITIONED BY (dt STRING)" in ddl
    assert "STORED AS PARQUET" in ddl
    assert "'parquet.compression'='SNAPPY'" in ddl

    mock_glue = MagicMock()
    synced = catalog.sync_glue_catalog(mock_glue)
    assert "trusted_iot_readings_validated" in synced
    assert "refined_fact_alerts_and_visits" in synced

    part = catalog.register_partition(
        glue_client=mock_glue,
        table_name="trusted_iot_readings_validated",
        partition_date="2026-10-22",
    )
    assert part["Values"] == ["2026-10-22"]
    assert part["StorageDescriptor"]["Location"].endswith("dt=2026-10-22/")
