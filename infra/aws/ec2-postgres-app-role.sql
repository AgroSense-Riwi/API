-- ==============================================================================
-- AgroSense Café — Rol de aplicación en el PostgreSQL+PostGIS de EC2 (ADR-001, RN-11)
--
-- Ejecutar UNA VEZ como superusuario (o como el dueño de las tablas con CREATEROLE),
-- ANTES de desplegar la versión de la API que usa agrosense_app. La contraseña se pasa
-- como variable de psql; nunca se guarda en el repositorio:
--
--   psql "host=<EC2_HOST> dbname=agrosense_db user=<SUPERUSER>" \
--        -v app_password="$(openssl rand -base64 32)" \
--        -v db_name=agrosense_db \
--        -v owner_role=<ROL_DUEÑO_DE_LAS_TABLAS> \
--        -f infra/aws/ec2-postgres-app-role.sql
--
-- Después:
--   1. Guardar la cadena postgresql+asyncpg://agrosense_app:<pass>@<host>:5432/agrosense_db
--      en el secreto agrosense/prod/database-url (AGROSENSE_DATABASE_URL de la API).
--   2. Mantener agrosense/prod/database-url-sync (DATABASE_URL, Alembic) con el rol dueño.
--   3. Si el rol dueño NO es superusuario, otorgarle BYPASSRLS para que Alembic y el seed
--      sigan escribiendo con FORCE ROW LEVEL SECURITY activo (requiere superusuario):
--        ALTER ROLE <ROL_DUEÑO_DE_LAS_TABLAS> BYPASSRLS;
-- ==============================================================================
\set ON_ERROR_STOP on

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
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO agrosense_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agrosense_app;

ALTER DEFAULT PRIVILEGES FOR ROLE :"owner_role" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agrosense_app;
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner_role" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO agrosense_app;

-- Verificación: debe devolver rolsuper = f, rolbypassrls = f.
SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'agrosense_app';
