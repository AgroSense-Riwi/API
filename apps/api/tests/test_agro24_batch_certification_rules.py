"""Tests automatizados para SCRUM-43:
Reglas de certificación de lotes y descarte por reservas forestales.

Cubre:
- SCRUM-159: Detección espacial de superposición con reservas forestales (RN-09).
- SCRUM-160: Verificación del 100% de volumen de origen trazable (RN-08).
- SCRUM-161: Máquina de estados de certificación (EN_PROCESO, CERTIFICADO, RECHAZADO).
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from agrosense_api.services.certification_engine import (
    BatchCertificationStateMachine,
    BatchCertificationStatus,
    ForestReserveSpatialIntersectionEngine,
    OriginVolumeVerificationRuleEngine,
)


def test_origin_volume_verification_exact_100_percent() -> None:
    """RN-08 / SCRUM-160: Lote con peso total igual a la suma de fincas aporta 100% trazable."""
    batch_id = uuid4()
    total_weight = Decimal("2500.00")
    compositions = [
        {"contributed_kg": Decimal("1000.00"), "farm_id": uuid4()},
        {"contributed_kg": Decimal("1500.00"), "farm_id": uuid4()},
    ]

    res = OriginVolumeVerificationRuleEngine.verify_lot_composition(
        batch_id=batch_id,
        total_weight_kg=total_weight,
        compositions=compositions,
    )

    assert res.is_100_percent_traced is True
    assert res.traceability_percentage == Decimal("100.00")
    assert res.discrepancy_kg == Decimal("0.00")


def test_origin_volume_verification_discrepancy_fails() -> None:
    """RN-08 / SCRUM-160: Discrepancia en peso total bloquea la certificación."""
    batch_id = uuid4()
    total_weight = Decimal("2500.00")
    # Solo suman 2000 kg (faltan 500 kg sin origen conocido)
    compositions = [
        {"contributed_kg": Decimal("1000.00"), "farm_id": uuid4()},
        {"contributed_kg": Decimal("1000.00"), "farm_id": uuid4()},
    ]

    res = OriginVolumeVerificationRuleEngine.verify_lot_composition(
        batch_id=batch_id,
        total_weight_kg=total_weight,
        compositions=compositions,
    )

    assert res.is_100_percent_traced is False
    assert res.traceability_percentage == Decimal("80.00")
    assert res.discrepancy_kg == Decimal("500.00")


def test_forest_reserve_overlap_detection_compliant() -> None:
    """RN-09 / SCRUM-159: Finca sin intersección espacial con reserva resulta compliant."""
    farm_id = uuid4()
    res = ForestReserveSpatialIntersectionEngine.evaluate_farm_polygons(
        farm_id=farm_id,
        farm_geojson_or_wkt={},
        known_overlaps=[{"has_overlap": False, "overlap_area_hectares": 0.0}],
    )

    assert res.is_compliant is True
    assert res.has_overlap is False
    assert res.overlap_area_hectares == Decimal("0.00")


def test_forest_reserve_overlap_detection_violation() -> None:
    """RN-09 / SCRUM-159: Finca con solapamiento en reserva forestal es rechazada."""
    farm_id = uuid4()
    res = ForestReserveSpatialIntersectionEngine.evaluate_farm_polygons(
        farm_id=farm_id,
        farm_geojson_or_wkt={},
        known_overlaps=[
            {
                "has_overlap": True,
                "overlap_area_hectares": 4.5,
                "reserve_name": "Parque Nacional Natural Los Nevados",
            }
        ],
    )

    assert res.is_compliant is False
    assert res.has_overlap is True
    assert res.overlap_area_hectares == Decimal("4.5")
    assert "Los Nevados" in str(res.reserve_name)


def test_certification_state_machine_approves_clean_lot() -> None:
    """SCRUM-161: Lote 100% trazable y sin solapamiento con reservas pasa a CERTIFICADO."""
    batch_id = uuid4()
    decision = BatchCertificationStateMachine.evaluate_batch(
        batch_id=batch_id,
        total_weight_kg=Decimal("1500.00"),
        compositions=[{"contributed_kg": Decimal("1500.00")}],
        farm_overlaps=[{"has_overlap": False}],
    )

    assert decision.current_status == BatchCertificationStatus.CERTIFICADO
    assert decision.is_certified is True
    assert decision.is_exportable is True
    assert decision.can_export is True
    assert len(decision.rejection_reasons) == 0


def test_certification_state_machine_rejects_violating_lot() -> None:
    """SCRUM-161: Lote con incumplimientos pasa a RECHAZADO y no puede exportarse."""
    batch_id = uuid4()
    decision = BatchCertificationStateMachine.evaluate_batch(
        batch_id=batch_id,
        total_weight_kg=Decimal("1500.00"),
        compositions=[{"contributed_kg": Decimal("1200.00")}],  # Faltan 300 kg
        farm_overlaps=[
            {
                "has_overlap": True,
                "overlap_area_hectares": 2.1,
                "reserve_name": "Santuario de Fauna y Flora Otún Quimbaya",
            }
        ],
    )

    assert decision.current_status == BatchCertificationStatus.RECHAZADO
    assert decision.is_certified is False
    assert decision.is_exportable is False
    assert decision.can_export is False
    assert len(decision.rejection_reasons) == 2
    assert any("RN-08" in r for r in decision.rejection_reasons)
    assert any("RN-09" in r for r in decision.rejection_reasons)


# ---------------------------------------------------------------------------
# RN-09: la ausencia de datos espaciales nunca certifica (fakes, sin BD)
# ---------------------------------------------------------------------------


def test_forest_reserve_without_any_evaluation_is_not_compliant() -> None:
    """RN-09: sin evaluación espacial (sin geometría/datos) la finca NO es conforme."""
    res = ForestReserveSpatialIntersectionEngine.evaluate_farm_polygons(
        farm_id=uuid4(), farm_geojson_or_wkt={}, known_overlaps=None
    )
    assert res.is_compliant is False
    assert res.evaluated is False


def test_forest_reserve_missing_geometry_is_not_compliant() -> None:
    res = ForestReserveSpatialIntersectionEngine.evaluate_farm_polygons(
        farm_id=uuid4(),
        farm_geojson_or_wkt={},
        known_overlaps=[{"has_overlap": False, "geometry_evaluated": False}],
    )
    assert res.is_compliant is False
    assert res.evaluated is False


def test_state_machine_rejects_lot_when_farm_has_no_spatial_evaluation() -> None:
    farm_ok, farm_unknown = uuid4(), uuid4()
    decision = BatchCertificationStateMachine.evaluate_batch(
        batch_id=uuid4(),
        total_weight_kg=Decimal("1000.00"),
        compositions=[
            {"farm_id": farm_ok, "contributed_kg": Decimal("600.00")},
            {"farm_id": farm_unknown, "contributed_kg": Decimal("400.00")},
        ],
        farm_overlaps=[{"farm_id": farm_ok, "has_overlap": False, "geometry_evaluated": True}],
    )
    assert decision.current_status == BatchCertificationStatus.RECHAZADO
    assert any(str(farm_unknown) in r and "RN-09" in r for r in decision.rejection_reasons)


def test_state_machine_rejects_lot_without_overlap_data() -> None:
    """Antes, farm_overlaps=None certificaba el lote; ahora se rechaza por falta de evidencia."""
    decision = BatchCertificationStateMachine.evaluate_batch(
        batch_id=uuid4(),
        total_weight_kg=Decimal("500.00"),
        compositions=[{"farm_id": uuid4(), "contributed_kg": Decimal("500.00")}],
        farm_overlaps=None,
    )
    assert decision.current_status == BatchCertificationStatus.RECHAZADO
    assert decision.can_export is False


def test_state_machine_certifies_when_every_farm_is_evaluated_without_overlap() -> None:
    farm_a, farm_b = uuid4(), uuid4()
    decision = BatchCertificationStateMachine.evaluate_batch(
        batch_id=uuid4(),
        total_weight_kg=Decimal("1000.00"),
        compositions=[
            {"farm_id": farm_a, "contributed_kg": Decimal("700.00")},
            {"farm_id": farm_b, "contributed_kg": Decimal("300.00")},
        ],
        farm_overlaps=[
            {"farm_id": farm_a, "has_overlap": False, "geometry_evaluated": True},
            {"farm_id": farm_b, "has_overlap": False, "geometry_evaluated": True},
        ],
    )
    assert decision.current_status == BatchCertificationStatus.CERTIFICADO


@pytest.mark.parametrize("bad_kg", [Decimal("-100.00"), Decimal("0.00")])
def test_origin_volume_rejects_non_positive_contribution(bad_kg: Decimal) -> None:
    """RN-08: un aporte negativo no puede compensar otros aportes para 'cuadrar' el 100%."""
    with pytest.raises(ValueError):
        OriginVolumeVerificationRuleEngine.verify_lot_composition(
            batch_id=uuid4(),
            total_weight_kg=Decimal("1000.00"),
            compositions=[
                {"contributed_kg": Decimal("1100.00")},
                {"contributed_kg": bad_kg},
            ],
        )


class _Row:
    def __init__(self, **values: object) -> None:
        self.__dict__.update(values)


class _FakeSpatialSession:
    def __init__(self, rows: list[_Row]) -> None:
        self.rows = rows
        self.params: dict | None = None

    async def execute(self, _statement, params):
        self.params = params
        return iter(self.rows)


@pytest.mark.anyio
async def test_fetch_farm_spatial_compliance_maps_postgis_rows() -> None:
    from agrosense_api.services.certification_engine import fetch_farm_spatial_compliance

    farm_overlap, farm_no_geom = uuid4(), uuid4()
    session = _FakeSpatialSession(
        [
            _Row(
                farm_id=farm_overlap,
                geometry_evaluated=True,
                has_overlap=True,
                overlap_area_hectares=1.234,
                reserve_name="PNN Los Nevados",
            ),
            _Row(
                farm_id=farm_no_geom,
                geometry_evaluated=False,
                has_overlap=False,
                overlap_area_hectares=0,
                reserve_name=None,
            ),
        ]
    )
    rows = await fetch_farm_spatial_compliance(session, [farm_overlap, farm_no_geom])

    assert session.params == {"farm_ids": [farm_overlap, farm_no_geom]}
    assert rows[0]["has_overlap"] is True
    assert rows[0]["overlap_area_hectares"] == Decimal("1.23")
    decision = BatchCertificationStateMachine.evaluate_batch(
        batch_id=uuid4(),
        total_weight_kg=Decimal("10.00"),
        compositions=[
            {"farm_id": farm_overlap, "contributed_kg": Decimal("5.00")},
            {"farm_id": farm_no_geom, "contributed_kg": Decimal("5.00")},
        ],
        farm_overlaps=rows,
    )
    assert decision.current_status == BatchCertificationStatus.RECHAZADO
    assert len(decision.rejection_reasons) == 2
