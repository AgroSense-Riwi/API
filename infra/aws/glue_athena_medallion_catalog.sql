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

-- ============================================================================
-- GOLD STAR SCHEMA & 6 BUSINESS QUESTIONS ANALYTICAL TABLES (E-11 / CT-10)
-- ============================================================================

CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.fact_visitas_agronomicas (
  visit_id STRING,
  date_key INT,
  farm_id STRING,
  agronomist_id STRING,
  rust_alerts_count INT,
  deficit_alerts_count INT,
  rain_alerts_count INT,
  priority_score DOUBLE,
  visit_status STRING,
  visit_outcome STRING
)
STORED AS PARQUET
LOCATION 's3://datalakeagrosense/gold/fact_visitas_agronomicas/'
TBLPROPERTIES ('parquet.compression'='SNAPPY', 'classification'='parquet');

CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.fact_iot_vs_satelite (
  comparison_id STRING,
  date_key INT,
  farm_id STRING,
  farm_code STRING,
  iot_temp_c DOUBLE,
  nasa_temp_c DOUBLE,
  temp_delta_c DOUBLE,
  iot_rh_pct DOUBLE,
  nasa_rh_pct DOUBLE,
  iot_precip_mm DOUBLE,
  open_meteo_precip_mm DOUBLE,
  sensor_fallback_active BOOLEAN
)
STORED AS PARQUET
LOCATION 's3://datalakeagrosense/gold/fact_iot_vs_satelite/'
TBLPROPERTIES ('parquet.compression'='SNAPPY', 'classification'='parquet');

CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.fact_lotes_exportacion (
  batch_id STRING,
  batch_code STRING,
  buyer_id STRING,
  buyer_name STRING,
  buyer_country STRING,
  total_weight_kg DOUBLE,
  traced_weight_kg DOUBLE,
  cupping_score DOUBLE,
  weighted_water_m3_ton DOUBLE,
  avg_fertilizer_red_pct DOUBLE,
  is_exportable BOOLEAN,
  is_certified BOOLEAN,
  is_deforestation_free BOOLEAN
)
STORED AS PARQUET
LOCATION 's3://datalakeagrosense/gold/fact_lotes_exportacion/'
TBLPROPERTIES ('parquet.compression'='SNAPPY', 'classification'='parquet');

CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.dim_sensores_salud (
  sensor_id STRING,
  sensor_code STRING,
  farm_id STRING,
  sensor_type STRING,
  status STRING,
  battery_level DOUBLE,
  anomaly_type STRING
)
STORED AS PARQUET
LOCATION 's3://datalakeagrosense/gold/dim_sensores_salud/'
TBLPROPERTIES ('parquet.compression'='SNAPPY', 'classification'='parquet');
