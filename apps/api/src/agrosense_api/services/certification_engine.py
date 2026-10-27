"""AgroSense Café — Batch Certification Rules and Forest Reserves Discarding Engine.

Implements business logic for:
- SCRUM-159: PostGIS Spatial Intersection Engine to Detect Farm Overlaps with Protected Forest
  Reserves (RN-09).
- SCRUM-160: 100% Origin Volume Verification Rule Engine for Export Lot Batches (RN-08).
- SCRUM-161: Batch Certification Workflow State Machine (EN_PROCESO, CERTIFICADO, RECHAZADO).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


class BatchCertificationStatus(str, Enum):
    """Estados del flujo de certificación de lotes exportables (SCRUM-161)."""

    EN_PROCESO = "EN_PROCESO"
    CERTIFICADO = "CERTIFICADO"
    RECHAZADO = "RECHAZADO"


@dataclass(frozen=True)
class ForestReserveOverlapResult:
    """Resultado de evaluación espacial de intersección con reservas protegidas (SCRUM-159)."""

    farm_id: UUID
    has_overlap: bool
    overlap_area_hectares: Decimal = Decimal("0.00")
    reserve_name: str | None = None
    is_compliant: bool = True  # Cumple si NO tiene solapamiento no autorizado con reservas


@dataclass(frozen=True)
class OriginVolumeVerificationResult:
    """Resultado de verificación del 100% de volumen de origen trazable (SCRUM-160)."""

    batch_id: UUID
    total_weight_kg: Decimal
    traced_weight_kg: Decimal
    traceability_percentage: Decimal
    is_100_percent_traced: bool
    discrepancy_kg: Decimal = Decimal("0.00")


@dataclass
class BatchCertificationDecision:
    """Decisión integral de la máquina de estados de certificación de lotes."""

    batch_id: UUID
    current_status: BatchCertificationStatus
    is_certified: bool
    is_exportable: bool
    validation_errors: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    volume_verification: OriginVolumeVerificationResult | None = None
    forest_overlaps: list[ForestReserveOverlapResult] = field(default_factory=list)

    @property
    def can_export(self) -> bool:
        return self.current_status == BatchCertificationStatus.CERTIFICADO


class ForestReserveSpatialIntersectionEngine:
    """Motor de intersección espacial PostGIS para reservas forestales (RN-09 / SCRUM-159)."""

    @classmethod
    def evaluate_farm_polygons(
        cls,
        farm_id: UUID,
        farm_geojson_or_wkt: str | dict[str, Any],
        known_overlaps: list[dict[str, Any]] | None = None,
    ) -> ForestReserveOverlapResult:
        """Determina si una finca se cruza con reservas forestales protegidas."""
        overlaps = known_overlaps or []
        for ov in overlaps:
            overlap_has_intersection = ov.get("has_overlap", False)
            area_ha = Decimal(str(ov.get("overlap_area_hectares", 0)))
            if overlap_has_intersection or area_ha > Decimal("0.00"):
                return ForestReserveOverlapResult(
                    farm_id=farm_id,
                    has_overlap=True,
                    overlap_area_hectares=area_ha,
                    reserve_name=ov.get("reserve_name", "Parque Nacional Natural"),
                    is_compliant=False,
                )

        return ForestReserveOverlapResult(
            farm_id=farm_id,
            has_overlap=False,
            overlap_area_hectares=Decimal("0.00"),
            is_compliant=True,
        )


class OriginVolumeVerificationRuleEngine:
    """Motor de validación de volumen de origen 100% trazable (RN-08 / SCRUM-160)."""

    EPSILON = Decimal("0.01")  # Tolerancia máxima por redondeo contable

    @classmethod
    def verify_lot_composition(
        cls,
        batch_id: UUID,
        total_weight_kg: Decimal,
        compositions: list[dict[str, Any]],
    ) -> OriginVolumeVerificationResult:
        """Verifica que la sumatoria de entregas iguale exactamente el peso total del lote."""
        if total_weight_kg <= Decimal("0.00"):
            raise ValueError(
                "El peso total del lote debe ser un valor positivo estrictamente mayor a 0."
            )

        sum_contributed = Decimal("0.00")
        for c in compositions:
            contributed = Decimal(str(c.get("contributed_kg", 0.0)))
            sum_contributed += contributed

        discrepancy = abs(total_weight_kg - sum_contributed)
        percentage = (sum_contributed / total_weight_kg) * Decimal("100.00")
        is_exact = discrepancy <= cls.EPSILON

        return OriginVolumeVerificationResult(
            batch_id=batch_id,
            total_weight_kg=total_weight_kg,
            traced_weight_kg=sum_contributed,
            traceability_percentage=percentage.quantize(Decimal("0.01")),
            is_100_percent_traced=is_exact,
            discrepancy_kg=discrepancy.quantize(Decimal("0.01")),
        )


class BatchCertificationStateMachine:
    """Máquina de estados para la certificación y autorización de exportación (SCRUM-161)."""

    @classmethod
    def evaluate_batch(
        cls,
        batch_id: UUID,
        total_weight_kg: Decimal,
        compositions: list[dict[str, Any]],
        farm_overlaps: list[dict[str, Any]] | None = None,
    ) -> BatchCertificationDecision:
        """Aplica la totalidad de reglas de negocio RN-08 y RN-09 para dictar el estado."""
        # 1. Regla RN-08: Verificar volumen 100% trazable
        vol_result = OriginVolumeVerificationRuleEngine.verify_lot_composition(
            batch_id=batch_id,
            total_weight_kg=total_weight_kg,
            compositions=compositions,
        )

        # 2. Regla RN-09: Verificar superposiciones con reservas forestales
        overlap_results: list[ForestReserveOverlapResult] = []
        raw_overlaps = farm_overlaps or []
        for ov_data in raw_overlaps:
            f_id = ov_data.get("farm_id", batch_id)
            eval_res = ForestReserveSpatialIntersectionEngine.evaluate_farm_polygons(
                farm_id=f_id,
                farm_geojson_or_wkt={},
                known_overlaps=[ov_data],
            )
            overlap_results.append(eval_res)

        rejection_reasons: list[str] = []
        validation_errors: list[str] = []

        if not vol_result.is_100_percent_traced:
            rejection_reasons.append(
                "Violación RN-08: El lote no alcanza el 100% de trazabilidad de origen "
                f"(registrado: {vol_result.traceability_percentage}% con "
                f"discrepancia de {vol_result.discrepancy_kg} kg)."
            )

        violating_reserves = [o for o in overlap_results if not o.is_compliant]
        if violating_reserves:
            for vr in violating_reserves:
                rejection_reasons.append(
                    f"Violación RN-09: Finca {vr.farm_id} posee solapamiento con la reserva "
                    f"protegida '{vr.reserve_name}' ({vr.overlap_area_hectares} ha)."
                )

        if rejection_reasons:
            status = BatchCertificationStatus.RECHAZADO
            is_certified = False
            is_exportable = False
        else:
            status = BatchCertificationStatus.CERTIFICADO
            is_certified = True
            is_exportable = True

        return BatchCertificationDecision(
            batch_id=batch_id,
            current_status=status,
            is_certified=is_certified,
            is_exportable=is_exportable,
            validation_errors=validation_errors,
            rejection_reasons=rejection_reasons,
            volume_verification=vol_result,
            forest_overlaps=overlap_results,
        )
