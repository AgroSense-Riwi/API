"""Tests automatizados para SCRUM-44:
Generación de certificados de trazabilidad JSON y métricas de huella hídrica.

Cubre:
- SCRUM-166: Construcción de certificado criptográfico JSON con GeoJSON.
- SCRUM-167: Cálculo de métricas de huella hídrica (L/kg y m3/ton).
- SCRUM-168: Validación pública de integridad criptográfica y detección de alteraciones.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from agrosense_api.services.certificate_service import (
    CertificateBuilder,
    DigitalTraceabilityCertificate,
    FarmGeoTraceabilityPayload,
    PublicCertificateValidator,
    WaterFootprintMetrics,
)


def test_water_footprint_calculation_metrics() -> None:
    """SCRUM-167: Calcula correctamente la huella hídrica en litros/kg y m3/ton."""
    batch_weight = Decimal("1000.00")  # 1 Tonelada
    irrigation_liters = Decimal("1500.00")
    rain_liters = Decimal("2500.00")

    metrics = WaterFootprintMetrics.calculate(
        total_batch_weight_kg=batch_weight,
        direct_irrigation_liters=irrigation_liters,
        satellite_rain_liters=rain_liters,
    )

    # 4000 Litros en total para 1000 kg => 4.00 L/kg
    assert metrics.total_water_liters == Decimal("4000.00")
    assert metrics.liters_per_kg == Decimal("4.00")
    assert metrics.m3_per_ton == Decimal("4.00")


def test_water_footprint_invalid_weight_fails() -> None:
    """SCRUM-167: Valida que pesos iguales o menores a cero lancen excepción."""
    with pytest.raises(ValueError, match="El peso total del lote debe ser estrictamente positivo"):
        WaterFootprintMetrics.calculate(
            total_batch_weight_kg=Decimal("0.00"),
            direct_irrigation_liters=Decimal("100.00"),
            satellite_rain_liters=Decimal("100.00"),
        )


def test_certificate_builder_creates_signed_certificate() -> None:
    """SCRUM-166: Genera un certificado firmado criptográficamente con GeoJSON incrustado."""
    batch_id = uuid4()
    farm_id = uuid4()
    water_metrics = WaterFootprintMetrics.calculate(
        total_batch_weight_kg=Decimal("1500.00"),
        direct_irrigation_liters=Decimal("2000.00"),
        satellite_rain_liters=Decimal("3000.00"),
    )

    farm_payload = FarmGeoTraceabilityPayload(
        farm_id=farm_id,
        farm_code="FCA-001",
        farm_name="Hacienda El Paraíso",
        contributed_kg=Decimal("1500.00"),
        share_percentage=Decimal("100.00"),
        geojson_polygon={
            "type": "Polygon",
            "coordinates": [[[-75.6, 4.5], [-75.5, 4.5], [-75.5, 4.6], [-75.6, 4.6], [-75.6, 4.5]]],
        },
    )

    cert = CertificateBuilder.build_certificate(
        batch_id=batch_id,
        batch_code="LOTE-EXP-2026",
        total_weight_kg=Decimal("1500.00"),
        water_metrics=water_metrics,
        farms=[farm_payload],
        cupping_score=Decimal("86.50"),
    )

    assert cert.certificate_hash != ""
    assert len(cert.certificate_hash) == 64  # SHA-256 hex string
    assert cert.batch_code == "LOTE-EXP-2026"
    assert len(cert.farms) == 1
    assert cert.farms[0]["geojson_polygon"]["type"] == "Polygon"


def test_public_certificate_validator_authentic() -> None:
    """SCRUM-168: Validador confirma autenticidad e integridad cuando el hash coincide."""
    batch_id = uuid4()
    water_metrics = WaterFootprintMetrics.calculate(
        total_batch_weight_kg=Decimal("1000.00"),
        direct_irrigation_liters=Decimal("500.00"),
        satellite_rain_liters=Decimal("1500.00"),
    )
    cert = CertificateBuilder.build_certificate(
        batch_id=batch_id,
        batch_code="LOTE-VAL-001",
        total_weight_kg=Decimal("1000.00"),
        water_metrics=water_metrics,
        farms=[],
    )

    cert_dict = cert.to_dict()
    val_res = PublicCertificateValidator.verify_certificate_integrity(cert_dict)

    assert val_res["is_valid"] is True
    assert val_res["status"] == "AUTHENTIC"
    assert val_res["batch_code"] == "LOTE-VAL-001"


def test_public_certificate_validator_detects_tampering() -> None:
    """SCRUM-168: Validador detecta cualquier alteración maliciosa en los datos."""
    batch_id = uuid4()
    water_metrics = WaterFootprintMetrics.calculate(
        total_batch_weight_kg=Decimal("1000.00"),
        direct_irrigation_liters=Decimal("500.00"),
        satellite_rain_liters=Decimal("1500.00"),
    )
    cert = CertificateBuilder.build_certificate(
        batch_id=batch_id,
        batch_code="LOTE-VAL-002",
        total_weight_kg=Decimal("1000.00"),
        water_metrics=water_metrics,
        farms=[],
    )

    cert_dict = cert.to_dict()
    # Modificación maliciosa del puntaje de catación
    cert_dict["cupping_score"] = 99.99

    val_res = PublicCertificateValidator.verify_certificate_integrity(cert_dict)

    assert val_res["is_valid"] is False
    assert val_res["status"] == "TAMPERED_SIGNATURE_MISMATCH"
