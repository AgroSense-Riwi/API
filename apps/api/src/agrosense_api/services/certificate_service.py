"""AgroSense Café — Traceability Certificate, Water Footprint and Public Validator.

Implements business logic for:
- SCRUM-166: Cryptographic JSON Traceability Certificate Builder
  with Embedded GeoJSON Farm Polygons.
- SCRUM-167: Water Footprint Calculation Module
  (Direct Irrigation + Satellite Rain per Kg Coffee).
- SCRUM-168: Public Verification API Endpoint and Digital Certificate Hash Validator.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class WaterFootprintMetrics:
    """Métricas de huella hídrica (SCRUM-167).

    Calcula la huella azul (riego directo) y verde (lluvia satelital)
    en Litros por Kilogramo de café.
    """

    direct_irrigation_liters: Decimal
    satellite_rain_liters: Decimal
    total_water_liters: Decimal
    total_batch_weight_kg: Decimal
    liters_per_kg: Decimal
    m3_per_ton: Decimal

    @classmethod
    def calculate(
        cls,
        total_batch_weight_kg: Decimal,
        direct_irrigation_liters: Decimal,
        satellite_rain_liters: Decimal,
    ) -> WaterFootprintMetrics:
        if total_batch_weight_kg <= Decimal("0.00"):
            raise ValueError("El peso total del lote debe ser estrictamente positivo.")

        total_water = direct_irrigation_liters + satellite_rain_liters
        l_per_kg = total_water / total_batch_weight_kg
        # 1 m3 = 1000 Litros, 1 Ton = 1000 Kg => 1 L/Kg equivale exactamente a 1 m3/Ton
        m3_per_ton = l_per_kg

        return WaterFootprintMetrics(
            direct_irrigation_liters=direct_irrigation_liters.quantize(Decimal("0.01")),
            satellite_rain_liters=satellite_rain_liters.quantize(Decimal("0.01")),
            total_water_liters=total_water.quantize(Decimal("0.01")),
            total_batch_weight_kg=total_batch_weight_kg.quantize(Decimal("0.01")),
            liters_per_kg=l_per_kg.quantize(Decimal("0.01")),
            m3_per_ton=m3_per_ton.quantize(Decimal("0.01")),
        )


@dataclass
class FarmGeoTraceabilityPayload:
    """Desglose de origen de finca con polígono GeoJSON incrustado."""

    farm_id: UUID
    farm_code: str
    farm_name: str
    contributed_kg: Decimal
    share_percentage: Decimal
    geojson_polygon: dict[str, Any] = field(default_factory=dict)


@dataclass
class DigitalTraceabilityCertificate:
    """Certificado digital de trazabilidad verificable mediante hash SHA-256 (SCRUM-166)."""

    certificate_id: str
    batch_id: UUID
    batch_code: str
    issued_at: str
    issuer: str
    buyer_id: UUID | None
    buyer_name: str | None
    total_weight_kg: Decimal
    cupping_score: Decimal | None
    water_footprint: dict[str, Any]
    farms: list[dict[str, Any]]
    certificate_hash: str = ""

    def compute_sha256(self) -> str:
        """Calcula el hash criptográfico canónico del certificado excluyendo certificate_hash."""
        payload = {
            "certificate_id": self.certificate_id,
            "batch_id": str(self.batch_id),
            "batch_code": self.batch_code,
            "issued_at": self.issued_at,
            "issuer": self.issuer,
            "buyer_id": str(self.buyer_id) if self.buyer_id else None,
            "buyer_name": self.buyer_name,
            "total_weight_kg": str(self.total_weight_kg),
            "cupping_score": str(self.cupping_score) if self.cupping_score is not None else None,
            "water_footprint": self.water_footprint,
            "farms": self.farms,
        }
        canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def sign_certificate(self) -> str:
        self.certificate_hash = self.compute_sha256()
        return self.certificate_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "certificate_id": self.certificate_id,
            "batch_id": str(self.batch_id),
            "batch_code": self.batch_code,
            "issued_at": self.issued_at,
            "issuer": self.issuer,
            "buyer_id": str(self.buyer_id) if self.buyer_id else None,
            "buyer_name": self.buyer_name,
            "total_weight_kg": float(self.total_weight_kg),
            "cupping_score": float(self.cupping_score) if self.cupping_score is not None else None,
            "water_footprint": self.water_footprint,
            "farms": self.farms,
            "certificate_hash": self.certificate_hash,
        }


class CertificateBuilder:
    """Generador canónico de certificados criptográficos (SCRUM-166)."""

    @classmethod
    def build_certificate(
        cls,
        batch_id: UUID,
        batch_code: str,
        total_weight_kg: Decimal,
        water_metrics: WaterFootprintMetrics,
        farms: list[FarmGeoTraceabilityPayload],
        buyer_id: UUID | None = None,
        buyer_name: str | None = None,
        cupping_score: Decimal | None = None,
        issuer: str = "AgroSense Café Certification Authority",
    ) -> DigitalTraceabilityCertificate:
        now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        cert_id = f"CERT-{batch_code}-{hashlib.md5(str(batch_id).encode()).hexdigest()[:8].upper()}"  # noqa: S324

        farms_payload = [
            {
                "farm_id": str(f.farm_id),
                "farm_code": f.farm_code,
                "farm_name": f.farm_name,
                "contributed_kg": str(f.contributed_kg),
                "share_percentage": str(f.share_percentage),
                "geojson_polygon": f.geojson_polygon,
            }
            for f in farms
        ]

        water_payload = {
            "direct_irrigation_liters": float(water_metrics.direct_irrigation_liters),
            "satellite_rain_liters": float(water_metrics.satellite_rain_liters),
            "total_water_liters": float(water_metrics.total_water_liters),
            "liters_per_kg": float(water_metrics.liters_per_kg),
            "m3_per_ton": float(water_metrics.m3_per_ton),
        }

        cert = DigitalTraceabilityCertificate(
            certificate_id=cert_id,
            batch_id=batch_id,
            batch_code=batch_code,
            issued_at=now_iso,
            issuer=issuer,
            buyer_id=buyer_id,
            buyer_name=buyer_name,
            total_weight_kg=total_weight_kg,
            cupping_score=cupping_score,
            water_footprint=water_payload,
            farms=farms_payload,
        )
        cert.sign_certificate()
        return cert


class PublicCertificateValidator:
    """Validador público de autenticidad e integridad de certificados (SCRUM-168)."""

    @classmethod
    def verify_certificate_integrity(
        cls, certificate_payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Comprueba si el certificado es auténtico o si ha sido manipulado."""
        stored_hash = certificate_payload.get("certificate_hash")
        if not stored_hash:
            return {
                "is_valid": False,
                "status": "TAMPERED_OR_MISSING_SIGNATURE",
                "message": "El certificado no contiene firma digital SHA-256.",
            }

        # Reconstruir objeto sin hash para verificar integridad
        water_dict = certificate_payload.get("water_footprint", {})
        buyer_id_raw = certificate_payload.get("buyer_id")
        buyer_uuid = UUID(buyer_id_raw) if buyer_id_raw else None
        cupping_raw = certificate_payload.get("cupping_score")
        cupping_val = Decimal(str(cupping_raw)) if cupping_raw is not None else None

        reconstructed = DigitalTraceabilityCertificate(
            certificate_id=certificate_payload.get("certificate_id", ""),
            batch_id=UUID(certificate_payload.get("batch_id")),
            batch_code=certificate_payload.get("batch_code", ""),
            issued_at=certificate_payload.get("issued_at", ""),
            issuer=certificate_payload.get("issuer", ""),
            buyer_id=buyer_uuid,
            buyer_name=certificate_payload.get("buyer_name"),
            total_weight_kg=Decimal(str(certificate_payload.get("total_weight_kg", "0"))),
            cupping_score=cupping_val,
            water_footprint=water_dict,
            farms=certificate_payload.get("farms", []),
        )

        expected_hash = reconstructed.compute_sha256()
        is_authentic = expected_hash == stored_hash

        return {
            "is_valid": is_authentic,
            "status": "AUTHENTIC" if is_authentic else "TAMPERED_SIGNATURE_MISMATCH",
            "batch_code": reconstructed.batch_code,
            "issuer": reconstructed.issuer,
            "computed_hash": expected_hash,
            "stored_hash": stored_hash,
        }
