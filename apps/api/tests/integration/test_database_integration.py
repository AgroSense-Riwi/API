import os
from datetime import date
from decimal import Decimal

import pytest
from agrosense_api.models import (
    Base,
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
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from geoalchemy2.shape import to_shape
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

# Usa TEST_DATABASE_URL o una base de datos PostgreSQL/PostGIS local por defecto
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg:///agrosense_test_db",
)


@pytest.fixture(scope="module")
def db_engine():
    """Inicializa el motor PostgreSQL/PostGIS y crea las 13 tablas del modelo."""
    try:
        engine = create_engine(TEST_DATABASE_URL, echo=False)
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto;"))
    except Exception as exc:
        pytest.skip(f"Base de datos PostgreSQL/PostGIS no disponible: {exc}")

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    yield engine

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine):
    """Proporciona una sesión transaccional aislada con rollback al finalizar cada test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection, expire_on_commit=False)
    session: Session = SessionLocal()

    yield session

    session.close()
    if transaction.is_active:
        transaction.rollback()
    connection.close()


def test_full_traceability_relational_chain(db_session: Session):
    """
    Verifica la persistencia y navegación ORM de la cadena completa:
    Producer ➔ Municipality ➔ Farm ➔ CoffeeVariety ➔ FarmPlot ➔
    HarvestDelivery ➔ ExportBatch ➔ BatchFarmComposition (+ RN-08, RN-09, RN-11).
    """
    # 1. Crear Productor y Municipio
    producer = Producer(
        document_number="CC-1088293847",
        full_name="Carlos Arturo Valencia",
        phone_number="+573104558899",
        cooperative_join_date=date(2019, 4, 15),
    )
    municipality = Municipality(name="Pitalito - Huila")
    db_session.add_all([producer, municipality])
    db_session.flush()

    # 2. Crear Variedad de Café y Finca
    variety = CoffeeVariety(variety_name="Castillo Naranjal")
    farm = Farm(
        farm_code="FIN-HUI-0001",
        producer_id=producer.producer_id,
        municipality_id=municipality.municipality_id,
        latitude=Decimal("1.853610"),
        longitude=Decimal("-76.050830"),
        cultivated_area_ha=Decimal("12.50"),
        water_usage_m3_ton=Decimal("4.20"),
        overlaps_reserve=True,
    )
    db_session.add_all([variety, farm])
    db_session.flush()

    # 3. Registrar superposición espacial PostGIS (RN-09) y Parcela (FarmPlot)
    reserve_overlap = FarmReserveOverlap(
        farm_id=farm.farm_id,
        reserve_name="Parque Nacional Natural Cueva de los Guácharos",
        overlap_geometry=WKTElement(
            "POLYGON((-76.0510 1.8530, -76.0500 1.8530, "
            "-76.0500 1.8540, -76.0510 1.8540, -76.0510 1.8530))",
            srid=4326,
        ),
    )
    plot = FarmPlot(
        farm_id=farm.farm_id,
        variety_id=variety.variety_id,
        plot_name="Lote El Mirador",
        crop_age_years=4,
    )
    db_session.add_all([reserve_overlap, plot])
    db_session.flush()

    # 4. Crear Entrega de Cosecha (HarvestDelivery)
    delivery = HarvestDelivery(
        farm_id=farm.farm_id,
        plot_id=plot.plot_id,
        delivery_date=date(2026, 10, 10),
        delivered_weight_kg=Decimal("1500.00"),
    )
    db_session.add(delivery)
    db_session.flush()

    # 5. Crear Lote de Exportación y Desglose de Composición 100% Trazable (RN-08)
    export_batch = ExportBatch(
        batch_code="EXP-COL-2026-001",
        total_weight_kg=Decimal("1500.00"),
        traced_weight_kg=Decimal("1500.00"),
        is_exportable=True,
        is_certified=True,
        weighted_water_m3_ton=Decimal("4.20"),
    )
    db_session.add(export_batch)
    db_session.flush()

    composition = BatchFarmComposition(
        batch_id=export_batch.batch_id,
        delivery_id=delivery.delivery_id,
        farm_id=farm.farm_id,
        contributed_kg=Decimal("1500.00"),
        share_percentage=Decimal("100.00"),
    )
    db_session.add(composition)

    # 6. Registrar Usuario, Catación SCA y Contrato de Comprador Internacional (RN-11)
    quality_chief = User(
        full_name="Dra. Elena Gómez",
        role="QUALITY_CHIEF",
    )
    buyer = InternationalBuyer(
        client_id="BUYER-EU-042",
        buyer_name="Nordic Specialty Roasters AS",
    )
    db_session.add_all([quality_chief, buyer])
    db_session.flush()

    cupping = CuppingEvaluation(
        batch_id=export_batch.batch_id,
        quality_chief_id=quality_chief.user_id,
        sca_total_score=Decimal("86.75"),
        acidity_score=Decimal("8.50"),
        body_score=Decimal("8.25"),
        sensory_notes="Notas a panela, jazmín y acidez cítrica brillante.",
    )
    contract = BuyerBatchContract(
        batch_id=export_batch.batch_id,
        buyer_id=buyer.buyer_id,
    )
    db_session.add_all([cupping, contract])
    db_session.commit()
    db_session.expire_all()

    # ==================== ASSERTIONS ====================
    stmt = select(ExportBatch).where(ExportBatch.batch_code == "EXP-COL-2026-001")
    persisted_batch = db_session.execute(stmt).scalar_one()

    assert persisted_batch.batch_id is not None
    assert persisted_batch.total_weight_kg == Decimal("1500.00")
    assert len(persisted_batch.compositions) == 1

    persisted_comp = persisted_batch.compositions[0]
    assert persisted_comp.share_percentage == Decimal("100.00")
    assert persisted_comp.delivery.plot.plot_name == "Lote El Mirador"
    assert persisted_comp.delivery.plot.variety.variety_name == "Castillo Naranjal"
    assert persisted_comp.farm.producer.document_number == "CC-1088293847"
    assert persisted_comp.farm.municipality.name == "Pitalito - Huila"

    # Verificar geometría PostGIS (RN-09)
    assert len(persisted_comp.farm.reserve_overlaps) == 1
    shapely_geom = to_shape(persisted_comp.farm.reserve_overlaps[0].overlap_geometry)
    assert shapely_geom.geom_type == "Polygon"
    assert shapely_geom.is_valid

    # Verificar evaluación de catación y contrato con comprador internacional (RN-11)
    assert len(persisted_batch.cupping_evaluations) == 1
    assert persisted_batch.cupping_evaluations[0].sca_total_score == Decimal("86.75")
    assert len(persisted_batch.contracts) == 1
    assert persisted_batch.contracts[0].buyer.client_id == "BUYER-EU-042"


def test_check_constraints_and_foreign_key_rules(db_session: Session):
    """Verifica que las restricciones CHECK y ON DELETE RESTRICT funcionen en PostgreSQL."""
    # 1. Violación de CheckConstraint: total_weight_kg <= 0 en ExportBatch
    invalid_batch = ExportBatch(
        batch_code="EXP-INVALID-001",
        total_weight_kg=Decimal("-50.00"),
    )
    db_session.add(invalid_batch)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    # 2. Violación de ON DELETE RESTRICT al borrar un comprador con contrato activo (RN-11)
    valid_batch = ExportBatch(
        batch_code="EXP-VALID-002",
        total_weight_kg=Decimal("500.00"),
    )
    buyer = InternationalBuyer(
        client_id="BUYER-US-999",
        buyer_name="Pacific Coffee Imports LLC",
    )
    db_session.add_all([valid_batch, buyer])
    db_session.flush()

    contract = BuyerBatchContract(
        batch_id=valid_batch.batch_id,
        buyer_id=buyer.buyer_id,
    )
    db_session.add(contract)
    db_session.flush()

    # Intentar eliminar el comprador directamente en BD debe lanzar IntegrityError por RESTRICT
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("DELETE FROM international_buyers WHERE buyer_id = :b_id"),
            {"b_id": buyer.buyer_id},
        )
    db_session.rollback()


def test_alembic_migration_upgrade_and_downgrade_reproducibility(db_engine):
    """
    Verifica la reproducibilidad y reversibilidad de las migraciones (Jira CT-06):
    Ejecuta 'alembic upgrade head' seguido de 'alembic downgrade base'.
    """
    alembic_ini_path = os.path.realpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")
    )
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    alembic_cfg = Config(alembic_ini_path)
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)

    # 1. Limpiar cualquier tabla existente para probar desde una BD limpia
    Base.metadata.drop_all(bind=db_engine)
    with db_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version;"))

    # 2. Ejecutar upgrade a head para verificar construcción limpia con Alembic
    command.upgrade(alembic_cfg, "head")

    # 3. Ejecutar downgrade a base para asegurar reversibilidad limpia
    command.downgrade(alembic_cfg, "base")

    # 4. Dejar el esquema en head
    command.upgrade(alembic_cfg, "head")
