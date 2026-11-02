-- ============================================================================
-- AgroSense Café — AWS Glue Data Catalog & Athena DDL (AGRO-14 / SCRUM-143)
-- Bucket: s3://datalakeagrosense/ (Region: us-east-2)
-- Format: Apache Parquet + Snappy Compression | Partitioned by dt (YYYY-MM-DD)
-- ============================================================================

CREATE DATABASE IF NOT EXISTS agrosense_datalake
COMMENT 'AgroSense Cafe S3 Medallion Data Lake Catalog (AGRO-14)';

CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.trusted_iot_readings_validated (
  reading_hash_id STRING,
  sensor_id STRING,
  farm_id STRING,
  sensor_type STRING,
  corrected_timestamp TIMESTAMP,
  expected_slot_ts TIMESTAMP,
  deviation_minutes DOUBLE,
  is_within_15m_window BOOLEAN,
  is_sensor_stuck_24h BOOLEAN,
  is_valid_for_alerts BOOLEAN,
  standardized_value DOUBLE,
  canonical_unit STRING,
  partition_date STRING
)
PARTITIONED BY (dt STRING)
STORED AS PARQUET
LOCATION 's3://datalakeagrosense/trusted/iot_readings_validated/'
TBLPROPERTIES (
  'parquet.compression'='SNAPPY',
  'classification'='parquet',
  'projection.enabled'='true',
  'projection.dt.type'='date',
  'projection.dt.format'='yyyy-MM-dd',
  'projection.dt.range'='2024-01-01,NOW',
  'storage.location.template'='s3://datalakeagrosense/trusted/iot_readings_validated/dt=${dt}/'
);

CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.trusted_agroclimatic_daily_features (
  farm_id STRING,
  observation_date DATE,
  high_humidity_hours_gt85 INT,
  daily_mean_temp_c DOUBLE,
  soil_moisture_mean_pct DOUBLE,
  precip_accum_72h_mm DOUBLE,
  forecast_rain_next_5d_mm DOUBLE,
  consecutive_rust_days INT,
  consecutive_dry_soil_days INT,
  primary_source_used STRING
)
PARTITIONED BY (dt STRING)
STORED AS PARQUET
LOCATION 's3://datalakeagrosense/trusted/agroclimatic_daily_features/'
TBLPROPERTIES (
  'parquet.compression'='SNAPPY',
  'classification'='parquet'
);

CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.trusted_soil_analyses_normalized (
  analysis_id STRING,
  farm_id STRING,
  sample_date DATE,
  original_unit STRING,
  conversion_factor_applied DOUBLE,
  n_mg_kg DOUBLE,
  p_mg_kg DOUBLE,
  k_mg_kg DOUBLE,
  ph DOUBLE
)
PARTITIONED BY (dt STRING)
STORED AS PARQUET
LOCATION 's3://datalakeagrosense/trusted/soil_analyses_normalized/'
TBLPROPERTIES (
  'parquet.compression'='SNAPPY',
  'classification'='parquet'
);
