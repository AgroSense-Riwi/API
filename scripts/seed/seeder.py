"""AgroSense Café — Database Seeder for PostgreSQL/PostGIS.

Manages direct connection and persistent insertion of synthetic seed datasets
into PostgreSQL according to topological relational hierarchy across all 13 tables (CT-06).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from agrosense_api.models import (
    BaseModel,
    BatchFarmComposition,
    BuyerBatchContract,
    CoffeeVariety,
    CuppingEvaluation,
    ExportBatch,
    Farm,
    FarmPlot,
    FarmReserveOverlap,
    HarvestDelivery,
    InternationalBuyer,
    Municipality,
    Producer,
    User,
)
from geoalchemy2.elements import WKTElement
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

logger = logging.getLogger("agrosense.seeder")

ORDERED_TABLE_NAMES = [
    "municipalities",
    "coffee_varieties",
    "users",
    "producers",
    "international_buyers",
    "farms",
    "farm_reserve_overlaps",
    "farm_plots",
    "harvest_deliveries",
    "export_batches",
    "batch_farm_compositions",
    "cupping_evaluations",
    "buyer_batch_contracts",
]

MODEL_MAP: dict[str, type[BaseModel]] = {
    "municipalities": Municipality,
    "coffee_varieties": CoffeeVariety,
    "users": User,
    "producers": Producer,
    "international_buyers": InternationalBuyer,
    "farms": Farm,
    "farm_reserve_overlaps": FarmReserveOverlap,
    "farm_plots": FarmPlot,
    "harvest_deliveries": HarvestDelivery,
    "export_batches": ExportBatch,
    "batch_farm_compositions": BatchFarmComposition,
    "cupping_evaluations": CuppingEvaluation,
    "buyer_batch_contracts": BuyerBatchContract,
}

TRUNCATE_SQL = text(
    "TRUNCATE TABLE "
    "batch_farm_compositions, "
    "harvest_deliveries, "
    "farm_reserve_overlaps, "
    "farm_plots, "
    "cupping_evaluations, "
    "buyer_batch_contracts, "
    "export_batches, "
    "farms, "
    "producers, "
    "users, "
    "international_buyers, "
    "municipalities, "
    "coffee_varieties "
    "RESTART IDENTITY CASCADE;"
)


def resolve_database_url(explicit_url: str | None = None) -> str:
    """Resuelve la URL de conexión síncrona a PostgreSQL.

    Prioridad:
    1. Parámetro explícito CLI (--db-url).
    2. Variable de entorno DATABASE_URL (usada por Alembic con driver psycopg).
    3. Variable de entorno AGROSENSE_DATABASE_URL (adaptando driver asyncpg a psycopg).
    4. Fallback por defecto local.
    """
    if explicit_url:
        url = explicit_url
    elif os.getenv("DATABASE_URL"):
        url = os.environ["DATABASE_URL"]
    elif os.getenv("AGROSENSE_DATABASE_URL"):
        url = os.environ["AGROSENSE_DATABASE_URL"]
    else:
        url = "postgresql+psycopg://agrosense_user:changeme@localhost:5432/agrosense_db"

    # Convertir driver asyncpg a psycopg para operaciones síncronas de seed
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)

    return url


class DatabaseSeeder:
    """Orquestador de siembra e inserción persistente en base de datos PostgreSQL."""

    def __init__(self, database_url: str | None = None, echo: bool = False) -> None:
        """Inicializa el motor de base de datos SQLAlchemy."""
        self.database_url = resolve_database_url(database_url)
        self.engine: Engine = create_engine(self.database_url, echo=echo, pool_pre_ping=True)

    def reset_database(self) -> None:
        """Ejecuta limpieza atómica de las 13 tablas operacionales (TRUNCATE CASCADE)."""
        logger.info("Ejecutando TRUNCATE CASCADE sobre las 13 tablas operacionales...")
        dialect_name = self.engine.dialect.name
        if dialect_name == "postgresql":
            with self.engine.begin() as conn:
                conn.execute(TRUNCATE_SQL)
        else:
            with Session(self.engine) as session:
                for table in reversed(ORDERED_TABLE_NAMES):
                    session.execute(delete(MODEL_MAP[table]))
                session.commit()
        logger.info("Base de datos reseteada exitosamente.")

    def seed(
        self,
        dataset: dict[str, list[dict[str, Any]]],
        reset: bool = False,
    ) -> dict[str, int]:
        """Inserta de manera transaccional e idempotente todos los registros del dataset.

        Args:
            dataset: Diccionario con los registros sintéticos de cada entidad.
            reset: Si es True, limpia las tablas antes de comenzar la siembra.

        Returns:
            dict[str, int]: Conteo final de filas persistidas por tabla.
        """
        if reset:
            self.reset_database()

        logger.info("Iniciando inserción de semillas en PostgreSQL (%s)...", self.database_url)

        with Session(self.engine) as session:
            # 1. Catálogos base: Municipios
            muni_map: dict[str, int] = {}
            existing_munis = {
                m.name: m.municipality_id
                for m in session.execute(select(Municipality)).scalars().all()
            }
            for rec in dataset.get("municipalities", []):
                name = rec["name"]
                if name in existing_munis:
                    muni_map[name] = existing_munis[name]
                else:
                    muni = Municipality(name=name)
                    session.add(muni)
                    session.flush()
                    muni_map[name] = muni.municipality_id
                    existing_munis[name] = muni.municipality_id

            # 2. Catálogos base: Variedades de Café
            var_map: dict[str, int] = {}
            existing_vars = {
                v.variety_name: v.variety_id
                for v in session.execute(select(CoffeeVariety)).scalars().all()
            }
            for rec in dataset.get("coffee_varieties", []):
                v_name = rec["variety_name"]
                if v_name in existing_vars:
                    var_map[v_name] = existing_vars[v_name]
                else:
                    var = CoffeeVariety(variety_name=v_name)
                    session.add(var)
                    session.flush()
                    var_map[v_name] = var.variety_id
                    existing_vars[v_name] = var.variety_id

            # 3. Usuarios
            existing_user_ids = set(session.execute(select(User.user_id)).scalars().all())
            for rec in dataset.get("users", []):
                if rec["user_id"] not in existing_user_ids:
                    session.add(
                        User(
                            user_id=rec["user_id"],
                            full_name=rec["full_name"],
                            role=rec["role"],
                        )
                    )
                    existing_user_ids.add(rec["user_id"])

            # 4. Productores
            existing_prod_docs = set(
                session.execute(select(Producer.document_number)).scalars().all()
            )
            for rec in dataset.get("producers", []):
                if rec["document_number"] not in existing_prod_docs:
                    session.add(
                        Producer(
                            producer_id=rec["producer_id"],
                            document_number=rec["document_number"],
                            full_name=rec["full_name"],
                            phone_number=rec.get("phone_number"),
                            cooperative_join_date=rec["cooperative_join_date"],
                        )
                    )
                    existing_prod_docs.add(rec["document_number"])

            # 5. Compradores Internacionales
            existing_buyers = set(
                session.execute(select(InternationalBuyer.client_id)).scalars().all()
            )
            for rec in dataset.get("international_buyers", []):
                if rec["client_id"] not in existing_buyers:
                    session.add(
                        InternationalBuyer(
                            buyer_id=rec["buyer_id"],
                            client_id=rec["client_id"],
                            buyer_name=rec["buyer_name"],
                        )
                    )
                    existing_buyers.add(rec["client_id"])

            session.flush()

            # 6. Fincas
            existing_farms = set(session.execute(select(Farm.farm_code)).scalars().all())
            for rec in dataset.get("farms", []):
                if rec["farm_code"] not in existing_farms:
                    m_id = muni_map.get(rec["municipality_name"], 1)
                    session.add(
                        Farm(
                            farm_id=rec["farm_id"],
                            farm_code=rec["farm_code"],
                            producer_id=rec["producer_id"],
                            municipality_id=m_id,
                            latitude=rec["latitude"],
                            longitude=rec["longitude"],
                            cultivated_area_ha=rec["cultivated_area_ha"],
                            water_usage_m3_ton=rec.get("water_usage_m3_ton"),
                            overlaps_reserve=rec.get("overlaps_reserve", False),
                        )
                    )
                    existing_farms.add(rec["farm_code"])

            session.flush()

            # 7. Superposiciones con Reservas Forestales (PostGIS)
            existing_overlaps = set(
                session.execute(select(FarmReserveOverlap.overlap_id)).scalars().all()
            )
            for rec in dataset.get("farm_reserve_overlaps", []):
                if rec["overlap_id"] not in existing_overlaps:
                    session.add(
                        FarmReserveOverlap(
                            overlap_id=rec["overlap_id"],
                            farm_id=rec["farm_id"],
                            reserve_name=rec["reserve_name"],
                            overlap_geometry=WKTElement(rec["overlap_geometry"], srid=4326),
                        )
                    )
                    existing_overlaps.add(rec["overlap_id"])

            # 8. Parcelas de cultivo (FarmPlots)
            existing_plots = set(session.execute(select(FarmPlot.plot_id)).scalars().all())
            for rec in dataset.get("farm_plots", []):
                if rec["plot_id"] not in existing_plots:
                    v_id = var_map.get(rec["variety_name"], 1)
                    session.add(
                        FarmPlot(
                            plot_id=rec["plot_id"],
                            farm_id=rec["farm_id"],
                            variety_id=v_id,
                            plot_name=rec["plot_name"],
                            crop_age_years=rec["crop_age_years"],
                        )
                    )
                    existing_plots.add(rec["plot_id"])

            session.flush()

            # 9. Lotes de Exportación (ExportBatches)
            existing_batches = set(session.execute(select(ExportBatch.batch_code)).scalars().all())
            for rec in dataset.get("export_batches", []):
                if rec["batch_code"] not in existing_batches:
                    session.add(
                        ExportBatch(
                            batch_id=rec["batch_id"],
                            batch_code=rec["batch_code"],
                            total_weight_kg=rec["total_weight_kg"],
                            traced_weight_kg=rec.get("traced_weight_kg", rec["total_weight_kg"]),
                            is_exportable=rec.get("is_exportable", True),
                            is_certified=rec.get("is_certified", True),
                            weighted_water_m3_ton=rec.get("weighted_water_m3_ton"),
                        )
                    )
                    existing_batches.add(rec["batch_code"])

            # 10. Entregas de Cosecha (HarvestDeliveries)
            existing_deliveries = set(
                session.execute(select(HarvestDelivery.delivery_id)).scalars().all()
            )
            for rec in dataset.get("harvest_deliveries", []):
                if rec["delivery_id"] not in existing_deliveries:
                    session.add(
                        HarvestDelivery(
                            delivery_id=rec["delivery_id"],
                            farm_id=rec["farm_id"],
                            plot_id=rec["plot_id"],
                            delivery_date=rec["delivery_date"],
                            delivered_weight_kg=rec["delivered_weight_kg"],
                        )
                    )
                    existing_deliveries.add(rec["delivery_id"])

            session.flush()

            # 11. Desglose de Composición del Lote (BatchFarmCompositions)
            existing_comps = set(
                session.execute(select(BatchFarmComposition.composition_id)).scalars().all()
            )
            for rec in dataset.get("batch_farm_compositions", []):
                if rec["composition_id"] not in existing_comps:
                    session.add(
                        BatchFarmComposition(
                            composition_id=rec["composition_id"],
                            batch_id=rec["batch_id"],
                            delivery_id=rec["delivery_id"],
                            farm_id=rec["farm_id"],
                            contributed_kg=rec["contributed_kg"],
                            share_percentage=rec["share_percentage"],
                        )
                    )
                    existing_comps.add(rec["composition_id"])

            # 12. Evaluaciones de Catación SCA (CuppingEvaluations)
            existing_cuppings = set(
                session.execute(select(CuppingEvaluation.cupping_id)).scalars().all()
            )
            for rec in dataset.get("cupping_evaluations", []):
                if rec["cupping_id"] not in existing_cuppings:
                    session.add(
                        CuppingEvaluation(
                            cupping_id=rec["cupping_id"],
                            batch_id=rec["batch_id"],
                            quality_chief_id=rec["quality_chief_id"],
                            sca_total_score=rec["sca_total_score"],
                            acidity_score=rec.get("acidity_score"),
                            body_score=rec.get("body_score"),
                            sensory_notes=rec.get("sensory_notes"),
                        )
                    )
                    existing_cuppings.add(rec["cupping_id"])

            # 13. Contratos de Compra de Lote (BuyerBatchContracts)
            existing_contracts = set(
                session.execute(select(BuyerBatchContract.contract_id)).scalars().all()
            )
            for rec in dataset.get("buyer_batch_contracts", []):
                if rec["contract_id"] not in existing_contracts:
                    session.add(
                        BuyerBatchContract(
                            contract_id=rec["contract_id"],
                            batch_id=rec["batch_id"],
                            buyer_id=rec["buyer_id"],
                        )
                    )
                    existing_contracts.add(rec["contract_id"])

            session.commit()
            logger.info("Transacción de siembra completada y confirmada (COMMIT).")

        return self.get_database_row_counts()

    def get_database_row_counts(self) -> dict[str, int]:
        """Consulta y retorna el recuento exacto de filas en cada una de las 13 tablas."""
        counts: dict[str, int] = {}
        with Session(self.engine) as session:
            for table_name, model_cls in MODEL_MAP.items():
                res = session.scalar(select(func.count()).select_from(model_cls))
                counts[table_name] = int(res or 0)
        return counts
