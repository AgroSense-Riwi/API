#!/usr/bin/env bash
set -euo pipefail

AIRFLOW_DB="${AIRFLOW_DB_NAME:-airflow_db}"

echo "[AgroSense Init] Habilitando extensiones postgis y pgcrypto en '${POSTGRES_DB}'..."
psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS pgcrypto;
EOSQL

echo "[AgroSense Init] Verificando/creando base de datos de metadatos de Airflow ('${AIRFLOW_DB}')..."
DB_EXISTS=$(psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" -tAc "SELECT 1 FROM pg_database WHERE datname='${AIRFLOW_DB}'")

if [ "${DB_EXISTS}" != "1" ]; then
    psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" -c "CREATE DATABASE \"${AIRFLOW_DB}\" OWNER \"${POSTGRES_USER}\";"
    echo "[AgroSense Init] Base de datos '${AIRFLOW_DB}' creada exitosamente."
else
    echo "[AgroSense Init] La base de datos '${AIRFLOW_DB}' ya existe."
fi
