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
  farm_id STRING,
  farm_code STRING,
  municipality_name STRING,
  department_name STRING,
  agronomist_id STRING,
  visit_date STRING,
  visit_quarter STRING,
  days_since_prev_visit INT,
  alert_id STRING,
  alert_type STRING,
  alert_response_hours DOUBLE,
  recommendation_status STRING,
  fertilizer_applied BOOLEAN,
  yield_kg_ha_before DOUBLE,
  yield_kg_ha_after DOUBLE,
  yield_delta_pct DOUBLE,
  notes STRING
)
STORED AS PARQUET
LOCATION 's3://datalakeagrosense/gold/fact_visitas_agronomicas/'
TBLPROPERTIES ('parquet.compression'='SNAPPY', 'classification'='parquet');

CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.fact_iot_vs_satelite (
  comparison_id STRING,
  farm_id STRING,
  farm_code STRING,
  municipality_name STRING,
  department_name STRING,
  altitude_meters INT,
  observation_month STRING,
  has_pilot_sensors BOOLEAN,
  iot_temp_mean_c DOUBLE,
  nasa_power_temp_mean_c DOUBLE,
  temp_abs_error_c DOUBLE,
  iot_humidity_mean_pct DOUBLE,
  ideam_humidity_mean_pct DOUBLE,
  humidity_abs_error_pct DOUBLE,
  iot_soil_moisture_pct DOUBLE,
  era5_soil_moisture_pct DOUBLE,
  soil_moisture_abs_error_pct DOUBLE,
  pearson_r_correlation DOUBLE,
  microclimate_divergence_flag BOOLEAN
)
STORED AS PARQUET
LOCATION 's3://datalakeagrosense/gold/fact_iot_vs_satelite/'
TBLPROPERTIES ('parquet.compression'='SNAPPY', 'classification'='parquet');

CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.fact_lotes_exportacion (
  batch_id STRING,
  batch_code STRING,
  buyer_id STRING,
  buyer_company STRING,
  destination_country STRING,
  coffee_variety STRING,
  harvest_season STRING,
  export_date STRING,
  total_weight_kg DOUBLE,
  traced_weight_kg DOUBLE,
  contributing_farms_count INT,
  weighted_water_m3_ton DOUBLE,
  regional_benchmark_m3_ton DOUBLE,
  water_saving_pct DOUBLE,
  cupping_score_sca DOUBLE,
  fob_price_usd_kg DOUBLE,
  certification_status STRING,
  sha256_certificate_hash STRING
)
STORED AS PARQUET
LOCATION 's3://datalakeagrosense/gold/fact_lotes_exportacion/'
TBLPROPERTIES ('parquet.compression'='SNAPPY', 'classification'='parquet');

CREATE EXTERNAL TABLE IF NOT EXISTS agrosense_datalake.dim_sensores_salud (
  sensor_id STRING,
  sensor_code STRING,
  farm_id STRING,
  farm_code STRING,
  municipality_name STRING,
  department_name STRING,
  sensor_type STRING,
  model STRING,
  installed_at STRING,
  status STRING,
  battery_mv INT,
  rssi_dbm INT,
  expected_readings_30d INT,
  actual_readings_30d INT,
  uptime_30d_pct DOUBLE,
  anomalies_30d_count INT,
  dominant_anomaly_type STRING,
  requires_maintenance BOOLEAN
)
STORED AS PARQUET
LOCATION 's3://datalakeagrosense/gold/dim_sensores_salud/'
TBLPROPERTIES ('parquet.compression'='SNAPPY', 'classification'='parquet');
