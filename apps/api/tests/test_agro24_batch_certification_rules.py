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
