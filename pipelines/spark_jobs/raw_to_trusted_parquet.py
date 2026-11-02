"""AgroSense Café — Raw-to-Trusted Parquet + Snappy Pipeline Job (AGRO-14 / SCRUM-142).

Reads raw JSON telemetry blocks from `s3://<bucket>/raw/iot_telemetry/`, normalizes
clock drift and 15-minute sampling windows (RN-01, RN-02), and writes Snappy-compressed
Apache Parquet partitions to `s3://<bucket>/trusted/iot_readings_validated/dt=YYYY-MM-DD/`.
"""

from __future__ import annotations

from typing import Any

from agrosense_api.services.s3_medallion_parquet import (
    GlueDataCatalogManager,
    MedallionLayerLayout,
    RawToTrustedParquetConverter,
)


def run_raw_to_trusted_pipeline(
    s3_client: Any,
    glue_client: Any | None,
    raw_records: list[dict[str, Any]],
    bucket_name: str = "datalakeagrosense",
    database_name: str = "agrosense_datalake",
) -> dict[str, Any]:
    """Execute end-to-end Medallion structuring, Snappy Parquet conversion, and Glue sync."""
    layout = MedallionLayerLayout(bucket_name=bucket_name)
    layout_summary = layout.provision_s3_medallion_layout(s3_client)

    uploaded_uris = RawToTrustedParquetConverter.upload_trusted_partitions_to_s3(
        s3_client=s3_client,
        bucket_name=bucket_name,
        raw_payload=raw_records,
        dataset_name="iot_readings_validated",
    )

    synced_tables: list[str] = []
    if glue_client is not None:
        catalog = GlueDataCatalogManager(
            database_name=database_name,
            bucket_name=bucket_name,
        )
        synced_tables = catalog.sync_glue_catalog(glue_client)
        for uri in uploaded_uris:
            if "dt=" in uri:
                dt_val = uri.split("dt=")[1].split("/")[0]
                catalog.register_partition(
                    glue_client=glue_client,
                    table_name="trusted_iot_readings_validated",
                    partition_date=dt_val,
                )

    return {
        "bucket": bucket_name,
        "layout": layout_summary,
        "uploaded_parquet_uris": uploaded_uris,
        "glue_tables_synced": synced_tables,
    }
