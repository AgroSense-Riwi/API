"""AgroSense Café — Batch Certification Rules and Forest Reserves Discarding Engine.

Implements business logic for:
- SCRUM-159: PostGIS Spatial Intersection Engine to Detect Farm Overlaps with Protected Forest
  Reserves (RN-09). The spatial test runs in the database (``ST_Intersects`` of ``farms.geom``
  against ``forest_reserves.geom``); a farm without geometry or without a spatial evaluation is
  never considered compliant.
- SCRUM-160: 100% Origin Volume Verification Rule Engine for Export Lot Batches (RN-08).
- SCRUM-161: Batch Certification Workflow State Machine (EN_PROCESO, CERTIFICADO, RECHAZADO).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agrosense_api.models.batch import ExportBatch


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
    is_compliant: bool = True  # Cumple sólo si fue evaluada y NO se solapa con reservas
    evaluated: bool = True  # False => sin geometría / sin evaluación espacial (no conforme)


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


def _is_evaluated(overlap: dict[str, Any]) -> bool:
    """Una evaluación sólo cuenta si declara explícitamente su resultado espacial."""
    if overlap.get("geometry_evaluated") is False:
        return False
    return "has_overlap" in overlap or "overlap_area_hectares" in overlap


class ForestReserveSpatialIntersectionEngine:
    """Motor de intersección espacial PostGIS para reservas forestales (RN-09 / SCRUM-159)."""

    @classmethod
    def evaluate_farm_polygons(
        cls,
        farm_id: UUID,
        farm_geojson_or_wkt: str | dict[str, Any],
        known_overlaps: list[dict[str, Any]] | None = None,
    ) -> ForestReserveOverlapResult:
        """Determina si una finca se cruza con reservas forestales protegidas.

        ``known_overlaps`` son resultados de la evaluación espacial en PostGIS (ver
        :func:`fetch_farm_spatial_compliance`). Sin evaluación, o con la geometría ausente,
        el resultado es NO conforme: la ausencia de datos nunca certifica.
        """
        overlaps = known_overlaps or []
        for ov in overlaps:
            overlap_has_intersection = bool(ov.get("has_overlap", False))
            area_ha = Decimal(str(ov.get("overlap_area_hectares") or 0))
            if overlap_has_intersection or area_ha > Decimal("0.00"):
                return ForestReserveOverlapResult(
                    farm_id=farm_id,
                    has_overlap=True,
                    overlap_area_hectares=area_ha,
                    reserve_name=ov.get("reserve_name") or "Reserva forestal protegida",
                    is_compliant=False,
                )

        if not overlaps or not all(_is_evaluated(ov) for ov in overlaps):
            return ForestReserveOverlapResult(
                farm_id=farm_id,
                has_overlap=False,
                overlap_area_hectares=Decimal("0.00"),
                is_compliant=False,
                evaluated=False,
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
            # Igual que el CHECK ck_batch_compositions_contributed_kg_positive: un aporte
            # negativo o nulo compensaría artificialmente otros aportes (RN-08).
            if contributed <= Decimal("0.00"):
                raise ValueError(
                    "Cada aporte de finca (contributed_kg) debe ser estrictamente mayor a 0."
                )
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

        # 2. Regla RN-09: toda finca de origen necesita una evaluación espacial conforme.
        raw_overlaps = farm_overlaps or []
        by_farm: dict[Any, list[dict[str, Any]]] = {}
        for ov_data in raw_overlaps:
            by_farm.setdefault(ov_data.get("farm_id", batch_id), []).append(ov_data)
        for comp in compositions:
            farm_id = comp.get("farm_id")
            if farm_id is not None:
                by_farm.setdefault(farm_id, [])
        if not by_farm:
            # Sin fincas ni evaluaciones no hay evidencia RN-09: se registra como no evaluada.
            by_farm[batch_id] = []

        overlap_results = [
            ForestReserveSpatialIntersectionEngine.evaluate_farm_polygons(
                farm_id=f_id,
                farm_geojson_or_wkt={},
                known_overlaps=farm_ovs,
            )
            for f_id, farm_ovs in by_farm.items()
        ]

        rejection_reasons: list[str] = []
        validation_errors: list[str] = []

        if not vol_result.is_100_percent_traced:
            rejection_reasons.append(
                "Violación RN-08: El lote no alcanza el 100% de trazabilidad de origen "
                f"(registrado: {vol_result.traceability_percentage}% con "
                f"discrepancia de {vol_result.discrepancy_kg} kg)."
            )

        for vr in overlap_results:
            if vr.has_overlap:
                rejection_reasons.append(
                    f"Violación RN-09: Finca {vr.farm_id} posee solapamiento con la reserva "
                    f"protegida '{vr.reserve_name}' ({vr.overlap_area_hectares} ha)."
                )
            elif not vr.evaluated:
                rejection_reasons.append(
                    f"Violación RN-09: Finca {vr.farm_id} sin geometría o sin evaluación "
                    "espacial contra reservas forestales; no puede certificarse."
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


# ---------------------------------------------------------------------------
# Evaluación espacial en PostGIS (RN-09)
# ---------------------------------------------------------------------------

FARM_SPATIAL_COMPLIANCE_SQL = text(
    """
    SELECT
        f.farm_id,
        f.geom IS NOT NULL AS geometry_evaluated,
        (
            COALESCE(sp.has_overlap, false)
            OR f.overlaps_reserve
            OR EXISTS (SELECT 1 FROM farm_reserve_overlaps o WHERE o.farm_id = f.farm_id)
        ) AS has_overlap,
        COALESCE(sp.overlap_ha, 0) AS overlap_area_hectares,
        sp.reserve_names AS reserve_name
    FROM farms f
    LEFT JOIN LATERAL (
        SELECT
            bool_or(true) AS has_overlap,
            SUM(ST_Area(ST_Intersection(f.geom, r.geom)::geography)) / 10000.0 AS overlap_ha,
            string_agg(r.name, ', ' ORDER BY r.name) AS reserve_names
        FROM forest_reserves r
        WHERE f.geom IS NOT NULL AND ST_Intersects(f.geom, r.geom)
    ) sp ON true
    WHERE f.farm_id = ANY(:farm_ids)
    """
)


async def fetch_farm_spatial_compliance(
    session: AsyncSession, farm_ids: list[UUID]
) -> list[dict[str, Any]]:
    """Evalúa en PostGIS (``ST_Intersects``) cada finca contra ``forest_reserves``.

    Una finca también se considera solapada si ``farms.overlaps_reserve`` está marcado o tiene
    registros en ``farm_reserve_overlaps``. Fincas inexistentes no aparecen en el resultado y,
    por tanto, quedan sin evaluación (no conformes) en :class:`BatchCertificationStateMachine`.
    """
    if not farm_ids:
        return []
    result = await session.execute(FARM_SPATIAL_COMPLIANCE_SQL, {"farm_ids": list(farm_ids)})
    return [
        {
            "farm_id": row.farm_id,
            "geometry_evaluated": bool(row.geometry_evaluated),
            "has_overlap": bool(row.has_overlap),
            "overlap_area_hectares": Decimal(str(row.overlap_area_hectares or 0)).quantize(
                Decimal("0.01")
            ),
            "reserve_name": row.reserve_name,
        }
        for row in result
    ]


async def evaluate_batch_certification(
    session: AsyncSession, batch_id: UUID
) -> BatchCertificationDecision:
    """Evalúa RN-08 + RN-09 de un lote leyendo composición y geometría desde la BD.

    ``export_batches`` está protegido por RLS: la sesión debe tener el contexto de operador
    (``get_operator_db``) para ver el lote.
    """
    batch = await session.scalar(
        select(ExportBatch)
        .options(selectinload(ExportBatch.compositions))
        .where(ExportBatch.batch_id == batch_id)
    )
    if batch is None:
        raise LookupError(f"Lote de exportación {batch_id} no encontrado")

    compositions = [
        {"farm_id": comp.farm_id, "contributed_kg": comp.contributed_kg}
        for comp in batch.compositions
    ]
    farm_ids = sorted({c["farm_id"] for c in compositions if c["farm_id"] is not None}, key=str)
    overlaps = await fetch_farm_spatial_compliance(session, farm_ids)
    if any(c["farm_id"] is None for c in compositions):
        # Aporte sin finca de origen: imposible verificar RN-09 para ese volumen.
        overlaps.append({"farm_id": "sin-finca-de-origen", "geometry_evaluated": False})
    return BatchCertificationStateMachine.evaluate_batch(
        batch_id=batch.batch_id,
        total_weight_kg=batch.total_weight_kg,
        compositions=compositions,
        farm_overlaps=overlaps,
    )
