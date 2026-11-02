"""Tests automatizados para SCRUM-45 (AGRO-26):
Diseño del modelo dimensional Star Schema y pipeline Gold Layer en S3 Refined.

Cubre:
- SCRUM-183: Especificación dimensional (Fact_Entregas, Fact_Alertas, Dim_Finca, Dim_Tiempo).
- SCRUM-184: Generación de Apache Parquet con Snappy (CT-07).
- SCRUM-185: Verificación de DDL e integración con Amazon Athena / AWS Glue.
"""

from datetime import date
from unittest.mock import MagicMock
from uuid import uuid4

from agrosense_api.services.star_schema_gold import (
    ATHENA_GOLD_STAR_SCHEMA_DDL,
    StarSchemaGoldPipeline,
)


def test_dim_tiempo_generation_structure() -> None:
    """SCRUM-183: Dim_Tiempo genera granularidad diaria y claves numéricas YYYYMMDD."""
    records = StarSchemaGoldPipeline.generate_dim_tiempo(date(2026, 1, 1), date(2026, 1, 31))
    assert len(records) == 31

    first = records[0]
    assert first.date_key == 20260101
    assert first.year == 2026
    assert first.month == 1
    assert first.quarter == 1
    assert not first.is_weekend


def test_star_schema_parquet_serialization_with_snappy() -> None:
    """SCRUM-184 / CT-07: Tablas se serializan en Parquet binario comprimido con Snappy."""
    fincas = [
        {
            "farm_id": str(uuid4()),
            "farm_code": "FCA-01",
            "farm_name": "La Esperanza",
            "municipality_name": "Chinchiná",
            "altitude_meters": 1650.0,
            "total_area_hectares": 12.5,
            "latitude": 4.98,
            "longitude": -75.60,
        }
    ]

    parquet_bytes = StarSchemaGoldPipeline.write_table_to_parquet_bytes(fincas)
    assert len(parquet_bytes) > 0
    # Comprobar firma mágica de Parquet ('PAR1') al inicio y final
    assert parquet_bytes[:4] == b"PAR1"
    assert parquet_bytes[-4:] == b"PAR1"


def test_gold_star_schema_etl_execution_and_s3_upload() -> None:
    """SCRUM-184: Ejecución integral del ETL Gold Layer con subida a S3 mockeada."""
    mock_s3 = MagicMock()
    farm_id = str(uuid4())

    deliveries = [
        {
            "delivery_id": str(uuid4()),
            "delivery_date": "2026-10-15",
            "farm_id": farm_id,
            "variety_id": 1,
            "delivered_weight_kg": 1500.0,
            "is_organic": True,
            "batch_id": str(uuid4()),
            "batch_code": "LOTE-EXP-001",
        }
    ]
    alerts = [
        {
            "alert_id": str(uuid4()),
            "observation_date": "2026-10-15",
            "farm_id": farm_id,
            "alert_type": "COFFEE_RUST",
            "severity": "CRITICAL",
            "alert_status": "ACTIVE",
            "soil_moisture_mean_pct": 88.5,
            "precip_accum_72h_mm": 130.0,
        }
    ]
    farms = [
        {
            "farm_id": farm_id,
            "farm_code": "FCA-001",
            "farm_name": "Finca Bellavista",
            "municipality_name": "Manizales",
            "altitude_meters": 1750.0,
            "total_area_hectares": 15.0,
            "latitude": 5.06,
            "longitude": -75.51,
        }
    ]

    result = StarSchemaGoldPipeline.run_gold_star_schema_etl(
        raw_deliveries=deliveries,
        raw_alerts=alerts,
        raw_farms=farms,
        s3_client=mock_s3,
        bucket_name="datalakeagrosense",
    )

    assert result["status"] == "SUCCESS"
    assert result["dim_finca_count"] == 1
    assert result["fact_entregas_count"] == 1
    assert result["fact_alertas_count"] == 1
    assert mock_s3.put_object.call_count == 4


def test_athena_gold_ddl_syntax_and_tables() -> None:
    """SCRUM-185: Verificación de DDL Athena para compatibilidad con Power BI."""
    table_patterns = [
        "CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.dim_finca",
        "CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.dim_tiempo",
        "CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.fact_entregas",
        "CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.fact_alertas",
    ]
    for pattern in table_patterns:
        assert pattern in ATHENA_GOLD_STAR_SCHEMA_DDL

    assert "s3://datalakeagrosense/gold/" in ATHENA_GOLD_STAR_SCHEMA_DDL
    assert "'parquet.compression'='SNAPPY'" in ATHENA_GOLD_STAR_SCHEMA_DDL
