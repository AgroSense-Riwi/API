#!/usr/bin/env bash
# ==============================================================================
# Rol de aplicación para la API (RN-11 / postgres-docker.md: roles diferenciados).
#
# - agrosense_app: LOGIN, sin SUPERUSER ni BYPASSRLS y NO dueño de las tablas, por lo que
#   las políticas RLS (FORCE ROW LEVEL SECURITY) le aplican siempre.
# - POSTGRES_USER sigue siendo el dueño: ejecuta Alembic y el seed (Database-seed).
#
# La contraseña llega por AGROSENSE_APP_DB_PASSWORD (nunca en el repo). Este script sólo
# corre al inicializar un volumen vacío; con un volumen existente: docker compose down -v.
# ==============================================================================
set -euo pipefail

: "${AGROSENSE_APP_DB_PASSWORD:?AGROSENSE_APP_DB_PASSWORD is required to create the API role}"

echo "[AgroSense Init] Creando/actualizando el rol de aplicación 'agrosense_app'..."
psql -v ON_ERROR_STOP=1 \
    --username "${POSTGRES_USER}" \
    --dbname "${POSTGRES_DB}" \
    -v app_password="${AGROSENSE_APP_DB_PASSWORD}" \
    -v db_name="${POSTGRES_DB}" \
    -v owner_role="${POSTGRES_USER}" <<'EOSQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agrosense_app') THEN
        CREATE ROLE agrosense_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$$;
ALTER ROLE agrosense_app WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'app_password';
GRANT CONNECT ON DATABASE :"db_name" TO agrosense_app;
GRANT USAGE ON SCHEMA public TO agrosense_app;
-- Tablas que Alembic cree después (como dueño) quedan accesibles para la API.
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner_role" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agrosense_app;
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner_role" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO agrosense_app;
EOSQL
echo "[AgroSense Init] Rol 'agrosense_app' listo."
