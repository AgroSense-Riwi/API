"""AgroSense Café — Reproducible and Idempotent Data Seed Script.

CLI and programmatic runner connecting directly to PostgreSQL/PostGIS to seed
operational datasets with Faker and strict idempotency (Deliverable E-03, CT-06).
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from faker import Faker

from scripts.seed.faker_generators import AgroSenseFakerGenerator
from scripts.seed.seeder import DatabaseSeeder, resolve_database_url

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("agrosense.seed")


@dataclass(frozen=True)
class SeedConfig:
    """Configuración inmutable de ejecución para el proceso de seeding.

    Attributes:
        mode: Escala del dataset ('dev' para desarrollo local o 'prod' para volumen completo).
        seed: Semilla entera fija para generadores de números pseudoaleatorios (PRNG).
        reset: Indica si se debe ejecutar un borrado seguro (TRUNCATE CASCADE) previo a la carga.
        db_url: URL de conexión a la base de datos PostgreSQL (opcional).
        echo: Habilita el logging detallado de sentencias SQL emitidas por SQLAlchemy.
    """

    mode: str
    seed: int
    reset: bool = False
    db_url: str | None = None
    echo: bool = False


def set_reproducible_state(seed: int) -> None:
    """Fija la semilla en los generadores globales para garantizar reproducibilidad determinista."""
    logger.debug("Fijando estado reproducible con semilla=%d", seed)
    random.seed(seed)
    Faker.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos CLI."""
    env_mode = os.getenv("SEED_PROFILE", "dev").lower()
    default_mode = env_mode if env_mode in {"dev", "prod"} else "dev"

    try:
        default_seed = int(os.getenv("SEED_VALUE", os.getenv("SEED_RANDOM_STATE", "42")))
    except ValueError:
        default_seed = 42

    env_reset = os.getenv("SEED_RESET_BEFORE_RUN", "false").lower() in {"true", "1", "yes"}

    parser = argparse.ArgumentParser(
        prog="run_seed.py",
        description=(
            "AgroSense Café - Ejecutor reproducible de datos sintéticos. "
            "Puebla y persiste las 13 tablas en PostgreSQL/PostGIS."
        ),
    )

    parser.add_argument(
        "--mode",
        choices=["dev", "prod"],
        default=default_mode,
        help=f"Perfil de volumen: 'dev' (ligero) o 'prod' (completo). Por defecto: {default_mode}.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=default_seed,
        help=f"Semilla entera para determinismo del PRNG. Por defecto: {default_seed}.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        default=env_reset,
        help="Limpia las tablas y reinicia secuencias antes de sembrar (TRUNCATE CASCADE).",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="URL de conexión a PostgreSQL (sobrescribe DATABASE_URL).",
    )
    parser.add_argument(
        "--echo",
        action="store_true",
        default=False,
        help="Activa el logging SQL de SQLAlchemy.",
    )

    return parser


def parse_args(args: Sequence[str] | None = None) -> SeedConfig:
    """Parsea argumentos de línea de comandos."""
    parser = build_parser()
    parsed = parser.parse_args(args)
    return SeedConfig(
        mode=parsed.mode,
        seed=parsed.seed,
        reset=parsed.reset,
        db_url=parsed.db_url,
        echo=parsed.echo,
    )


def run_seed(
    config: SeedConfig | None = None,
    *,
    seed_value: int | None = None,
    mode: str = "dev",
    reset: bool = False,
    db_url: str | None = None,
    echo: bool = False,
) -> dict[str, int]:
    """Orquesta la generación e inserción real en la base de datos PostgreSQL.

    Args:
        config: Objeto SeedConfig opcional.
        seed_value: Semilla opcional si no se usa config.
        mode: Modo ('dev'/'prod') si no se usa config.
        reset: Limpieza previa si no se usa config.
        db_url: URL de BD opcional si no se usa config.
        echo: SQL echo si no se usa config.

    Returns:
        dict[str, int]: Conteo de filas persistidas por tabla en PostgreSQL.
    """
    if config is None:
        seed = seed_value if seed_value is not None else 42
        config = SeedConfig(
            mode=mode,
            seed=seed,
            reset=reset,
            db_url=db_url,
            echo=echo,
        )

    resolved_url = resolve_database_url(config.db_url)
    logger.info("Iniciando proceso de seeding en PostgreSQL...")
    logger.info(
        "Configuración activa: mode=%s, seed=%d, reset=%s, target_db=%s",
        config.mode,
        config.seed,
        config.reset,
        resolved_url.split("@")[-1] if "@" in resolved_url else resolved_url,
    )

    # 1. Asegurar determinismo
    set_reproducible_state(config.seed)

    # 2. Generación determinista del dataset sintético
    generator = AgroSenseFakerGenerator(seed=config.seed)
    dataset = generator.generate_all(mode=config.mode)
    logger.info(
        "Dataset sintético generado: %d fincas, %d productores, %d lotes de exportación.",
        len(dataset.get("farms", [])),
        len(dataset.get("producers", [])),
        len(dataset.get("export_batches", [])),
    )

    # 3. Conexión directa y persistencia en base de datos PostgreSQL
    seeder = DatabaseSeeder(database_url=config.db_url, echo=config.echo)
    final_counts = seeder.seed(dataset=dataset, reset=config.reset)

    logger.info("Resumen de registros persistidos en base de datos:")
    for table, count in final_counts.items():
        logger.info("  • %s: %d filas", table, count)

    return final_counts


def main(args: Sequence[str] | None = None) -> int:
    """Punto de entrada principal para la invocación CLI."""
    config = parse_args(args)
    try:
        run_seed(config)
        logger.info("Proceso de siembra finalizado exitosamente (Exit code 0).")
        return 0
    except Exception as exc:
        logger.exception("Error fatal durante el proceso de siembra en PostgreSQL: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
