import os
import sys
from logging.config import fileConfig

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import engine_from_config, pool, text

# Asegurar que el directorio 'src' esté en el PYTHONPATH
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "src")))

# Importar Base y todos los modelos para registrar las 13 tablas en Base.metadata
from agrosense_api.models import Base  # noqa: E402

# Objeto de configuración de Alembic (lee alembic.ini)
config = context.config

# Permitir sobrescribir sqlalchemy.url mediante variable de entorno DATABASE_URL
database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

# Configurar logging desde alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata objetivo para autogenerate
target_metadata = Base.metadata

# Tablas internas de PostGIS / Tiger Geocoder que Alembic debe ignorar
POSTGIS_SYSTEM_TABLES = {
    "spatial_ref_sys",
    "geography_columns",
    "geometry_columns",
    "raster_columns",
    "raster_overviews",
    "layer",
    "topology",
}


def include_object(object, name, type_, reflected, compare_to):
    """
    Filtro combinado: delega en geoalchemy2.alembic_helpers.include_object
    y descarta cualquier tabla de sistema de PostGIS.
    """
    if type_ == "table" and name in POSTGIS_SYSTEM_TABLES:
        return False
    return alembic_helpers.include_object(object, name, type_, reflected, compare_to)


def run_migrations_offline() -> None:
    """Ejecuta migraciones en modo 'offline' (generación de SQL sin conexión activa)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        render_item=alembic_helpers.render_item,
        process_revision_directives=alembic_helpers.writer,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Ejecuta migraciones en modo 'online' contra el motor PostgreSQL/PostGIS."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Garantizar que las extensiones requeridas existan en la base de datos
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto;"))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            render_item=alembic_helpers.render_item,
            process_revision_directives=alembic_helpers.writer,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()