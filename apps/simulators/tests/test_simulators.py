"""AgroSense Café — Test Suite for IoT Sensor Simulator (AGRO-15 / EP-05).

Validates:
1. Pydantic schema validation for JSON telemetry payload.
2. Deterministic UUIDv5 sensor mapping (900 sensors across 300 farms).
3. Physical generation constraints (temperature, soil moisture, precipitation).
4. DEF-01: Clock drift timestamp offset (-2h to +12h).
5. DEF-02: Stuck sensor variation < 0.5% over 24 continuous hours.
6. DEF-03 & RNF-01: Chronological burst buffer ordering and unified batch ID.
7. Depleted battery level (< 15%).
8. AMQP routing keys and resilient publisher behavior.
"""

from __future__ import annotations

import argparse
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from agrosense_simulators.amqp_publisher import RabbitMQPublisher
from agrosense_simulators.anomaly_injector import AnomalyInjector
from agrosense_simulators.config import (
    AGROSENSE_NAMESPACE,
    SENSOR_ROUTING_KEY_MAP,
    CanonicalUnit,
    SensorType,
    SimulatorConfig,
    TelemetryPayload,
)
from agrosense_simulators.main import run_simulation
from agrosense_simulators.sensor_generator import SensorNetwork, SensorNode
from pydantic import ValidationError


def test_telemetry_payload_schema_valid() -> None:
    """Verifica que un payload conforme sea validado correctamente por Pydantic."""
    raw_payload = {
        "sensor_id": "8f12c49a-0000-0000-0000-000000000001",
        "farm_id": "e7a3b110-0000-0000-0000-000000000001",
        "sensor_type": "SOIL_MOISTURE",
        "reading_value": 34.5,
        "canonical_unit": "%",
        "reading_timestamp": "2026-10-20T07:00:00Z",
        "ingestion_batch_id": "BATCH-20261020-001",
        "battery_level_pct": 88.5,
        "firmware_version": "v1.0.0",
    }
    payload = TelemetryPayload(**raw_payload)
    assert payload.sensor_id == raw_payload["sensor_id"]
    assert payload.reading_value == 34.5
    assert payload.canonical_unit == "%"
    assert payload.reading_timestamp.endswith("Z")
    assert payload.battery_level_pct == 88.5


def test_telemetry_payload_schema_invalid_timestamp() -> None:
    """Verifica que timestamps no UTC sean rechazados por el validador."""
    with pytest.raises(ValidationError):
        TelemetryPayload(
            sensor_id="8f12c49a-0000-0000-0000-000000000001",
            farm_id="e7a3b110-0000-0000-0000-000000000001",
            sensor_type="TEMPERATURE",
            reading_value=22.0,
            canonical_unit="CELSIUS",
            reading_timestamp="2026-10-20 07:00:00",  # Falta Z o offset UTC
            ingestion_batch_id="BATCH-001",
            battery_level_pct=90.0,
        )


def test_telemetry_payload_schema_invalid_battery() -> None:
    """Verifica que niveles de batería fuera del rango [0, 100] sean rechazados."""
    with pytest.raises(ValidationError):
        TelemetryPayload(
            sensor_id="8f12c49a-0000-0000-0000-000000000001",
            farm_id="e7a3b110-0000-0000-0000-000000000001",
            sensor_type="TEMPERATURE",
            reading_value=22.0,
            canonical_unit="CELSIUS",
            reading_timestamp="2026-10-20T07:00:00Z",
            ingestion_batch_id="BATCH-001",
            battery_level_pct=105.0,  # Inválido > 100
        )


def test_deterministic_sensor_mapping_900_sensors() -> None:
    """Verifica la generación determinista de 900 sensores en 300 fincas con UUIDv5."""
    network = SensorNetwork(farms_count=300, firmware_version="v1.0.0", seed=42)
    assert network.total_sensors == 900

    soil_sensors = network.get_sensors_by_type(SensorType.SOIL_MOISTURE)
    temp_sensors = network.get_sensors_by_type(SensorType.TEMPERATURE)
    pluv_sensors = network.get_sensors_by_type(SensorType.PLUVIOMETER)

    assert len(soil_sensors) == 300
    assert len(temp_sensors) == 300
    assert len(pluv_sensors) == 300

    # Validar que los UUIDv5 coincidan con el cálculo determinista
    for i in range(1, 10):
        farm_code = f"FIN-{i:04d}"
        expected_farm_uuid = str(uuid.uuid5(AGROSENSE_NAMESPACE, farm_code))

        for stype in [SensorType.SOIL_MOISTURE, SensorType.TEMPERATURE, SensorType.PLUVIOMETER]:
            natural_key = f"{farm_code}:{stype.value}"
            expected_sensor_uuid = str(uuid.uuid5(AGROSENSE_NAMESPACE, natural_key))

            matching = [
                s for s in network.sensors if s.farm_code == farm_code and s.sensor_type == stype
            ]
            assert len(matching) == 1
            node = matching[0]
            assert node.farm_id == expected_farm_uuid
            assert node.sensor_id == expected_sensor_uuid


def test_physical_generation_temperature_bounds_and_diurnal_cycle() -> None:
    """Verifica que la temperatura respete el ciclo diurno y el rango [15, 28] °C."""
    node = SensorNode(
        farm_code="FIN-0001",
        farm_id=str(uuid.uuid5(AGROSENSE_NAMESPACE, "FIN-0001")),
        sensor_type=SensorType.TEMPERATURE,
        sensor_id=str(uuid.uuid5(AGROSENSE_NAMESPACE, "FIN-0001:TEMPERATURE")),
        seed=42,
    )

    base_dt = datetime(2026, 10, 20, 0, 0, 0, tzinfo=UTC)
    temps_at_hours: dict[int, list[float]] = {}

    for minute_step in range(0, 24 * 60, 10):
        current_dt = base_dt + timedelta(minutes=minute_step)
        payload = node.step_reading(current_dt, batch_id="TEST-TEMP")

        assert 15.0 <= payload.reading_value <= 28.0
        assert payload.canonical_unit == CanonicalUnit.CELSIUS.value

        h = current_dt.hour
        temps_at_hours.setdefault(h, []).append(payload.reading_value)

    # Validar ciclo diurno: Tarde (14:00) debe ser sensiblemente más cálida que la madrugada (02:00)
    avg_dawn = sum(temps_at_hours[2]) / len(temps_at_hours[2])
    avg_afternoon = sum(temps_at_hours[14]) / len(temps_at_hours[14])
    assert avg_afternoon > avg_dawn
    assert avg_afternoon >= 24.0
    assert avg_dawn <= 19.0


def test_physical_generation_soil_moisture_bounds() -> None:
    """Verifica que la humedad del suelo se mantenga continua entre 15% y 55%."""
    node = SensorNode(
        farm_code="FIN-0002",
        farm_id=str(uuid.uuid5(AGROSENSE_NAMESPACE, "FIN-0002")),
        sensor_type=SensorType.SOIL_MOISTURE,
        sensor_id=str(uuid.uuid5(AGROSENSE_NAMESPACE, "FIN-0002:SOIL_MOISTURE")),
        seed=123,
    )

    base_dt = datetime(2026, 10, 20, 0, 0, 0, tzinfo=UTC)
    for step in range(144):  # 24 horas cada 10 min
        current_dt = base_dt + timedelta(minutes=step * 10)
        payload = node.step_reading(current_dt, batch_id="TEST-SOIL")
        assert 15.0 <= payload.reading_value <= 55.0
        assert payload.canonical_unit == CanonicalUnit.PERCENT.value


def test_physical_generation_pluviometer_bounds() -> None:
    """Verifica que la pluviometría oscile entre 0.0 mm y 45.0 mm."""
    node = SensorNode(
        farm_code="FIN-0003",
        farm_id=str(uuid.uuid5(AGROSENSE_NAMESPACE, "FIN-0003")),
        sensor_type=SensorType.PLUVIOMETER,
        sensor_id=str(uuid.uuid5(AGROSENSE_NAMESPACE, "FIN-0003:PLUVIOMETER")),
        seed=777,
    )

    base_dt = datetime(2026, 10, 20, 0, 0, 0, tzinfo=UTC)
    values: list[float] = []
    for step in range(500):  # Muestreo amplio para captar lluvia
        current_dt = base_dt + timedelta(minutes=step * 10)
        payload = node.step_reading(current_dt, batch_id="TEST-PLUV")
        assert 0.0 <= payload.reading_value <= 45.0
        assert payload.canonical_unit == CanonicalUnit.MM.value
        values.append(payload.reading_value)

    # La mayoría de los intervalos deben ser secos (0 mm)
    zero_rain_count = sum(1 for v in values if v == 0.0)
    assert zero_rain_count > len(values) * 0.70


def test_def01_clock_drift_injection() -> None:
    """Verifica que DEF-01 inyecte desfases de -2h a +12h en reading_timestamp."""
    node = SensorNode(
        farm_code="FIN-0010",
        farm_id="farm-uuid-10",
        sensor_type=SensorType.TEMPERATURE,
        sensor_id="sensor-uuid-10",
        seed=42,
    )
    # Forzar desfase de +4 horas (+14400s)
    node.clock_drift_seconds = 14400

    nominal_dt = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    payload = node.step_reading(nominal_dt, batch_id="TEST-DRIFT")

    # Timestamp generado debe reflejar 14:00:00Z en vez de 10:00:00Z
    assert payload.reading_timestamp == "2026-10-20T14:00:00Z"


def test_def02_stuck_sensor_low_variance_over_24h() -> None:
    """Verifica que DEF-02 reporte variación < 0.5% en 24h consecutivas (144 lecturas)."""
    node = SensorNode(
        farm_code="FIN-0015",
        farm_id="farm-uuid-15",
        sensor_type=SensorType.TEMPERATURE,
        sensor_id="sensor-uuid-15",
        seed=999,
    )
    node.is_stuck = True
    node.stuck_value = 22.50

    base_dt = datetime(2026, 10, 20, 0, 0, 0, tzinfo=UTC)
    readings: list[float] = []

    for step in range(144):  # 24 horas cada 10 min
        current_dt = base_dt + timedelta(minutes=step * 10)
        payload = node.step_reading(current_dt, batch_id="TEST-STUCK")
        readings.append(payload.reading_value)

    min_val = min(readings)
    max_val = max(readings)
    mean_val = sum(readings) / len(readings)

    # Variación porcentual: (max - min) / mean
    variation_pct = (max_val - min_val) / mean_val * 100.0
    assert variation_pct < 0.5, f"La variación {variation_pct}% debe ser < 0.5%"


def test_def03_and_rnf01_burst_buffer_chronological_order_and_batch_id() -> None:
    """Verifica que el búfer acumule 3 días de desconexión y emita en orden cronológico estricto."""
    node = SensorNode(
        farm_code="FIN-0020",
        farm_id="farm-uuid-20",
        sensor_type=SensorType.SOIL_MOISTURE,
        sensor_id="sensor-uuid-20",
        seed=555,
    )

    days_disconnected = 3
    intervals_per_day = 24 * 6  # 144 lecturas por día
    total_expected = days_disconnected * intervals_per_day  # 432 lecturas

    base_dt = datetime(2026, 10, 15, 0, 0, 0, tzinfo=UTC)

    # 1. Acumular lecturas en el búfer local durante el período de desconexión
    for i in range(total_expected):
        reading_dt = base_dt + timedelta(minutes=i * 10)
        payload = node.step_reading(reading_dt, batch_id="LOCAL-DISCONNECTED")
        node.buffer_reading(payload)

    assert len(node.chronological_buffer) == total_expected

    # 2. Reconexión celular: Vaciar el búfer en una ráfaga compartiendo ingestion_batch_id
    burst_batch_id = "BATCH-BURST-RECONNECT-001"
    burst_payloads = node.flush_burst_buffer(burst_batch_id=burst_batch_id)

    assert len(burst_payloads) == total_expected
    assert len(node.chronological_buffer) == 0

    # 3. Validar que todas las lecturas compartan el mismo ingestion_batch_id
    for p in burst_payloads:
        assert p.ingestion_batch_id == burst_batch_id

    # 4. Validar orden cronológico estricto (t0 < t1 < t2 ...)
    parsed_timestamps = [
        datetime.fromisoformat(p.reading_timestamp.replace("Z", "+00:00")) for p in burst_payloads
    ]
    for i in range(len(parsed_timestamps) - 1):
        assert parsed_timestamps[i] <= parsed_timestamps[i + 1], (
            f"Inversión temporal detectada en índice {i}"
        )


def test_depleted_battery_injection() -> None:
    """Verifica que el defecto de batería agotada reporte battery_level_pct < 15%."""
    config = SimulatorConfig(
        seed_defect_low_battery_rate=1.0,  # Forzar para todos
        seed_defect_clock_drift_rate=0.0,
        seed_defect_stuck_rate=0.0,
        seed_defect_disconnect_rate=0.0,
    )
    network = SensorNetwork(farms_count=5, seed=42)
    injector = AnomalyInjector(config)
    report = injector.apply_defects(network.sensors)

    assert report.low_battery_sensors_count == len(network.sensors)
    for s in network.sensors:
        assert s.battery_level_pct < 15.0
        payload = s.step_reading(datetime.now(UTC), batch_id="BATT-TEST")
        assert payload.battery_level_pct < 15.0


def test_amqp_routing_keys_mapping() -> None:
    """Verifica que cada tipo de sensor mapee a la Routing Key AMQP requerida."""
    assert SENSOR_ROUTING_KEY_MAP[SensorType.SOIL_MOISTURE] == "telemetry.block.soil_moisture"
    assert SENSOR_ROUTING_KEY_MAP[SensorType.TEMPERATURE] == "telemetry.block.temperature"
    assert SENSOR_ROUTING_KEY_MAP[SensorType.PLUVIOMETER] == "telemetry.block.pluviometer"


def test_rabbitmq_publisher_mock_mode() -> None:
    """Verifica el envío de mensajes individuales y en bloque con el publisher mock."""
    publisher = RabbitMQPublisher(
        rabbitmq_url="amqp://dummy:dummy@localhost:5672/",
        mock_mode=True,
    )
    assert publisher.connect() is True
    assert publisher.is_connected() is True

    payload = TelemetryPayload(
        sensor_id="8f12c49a-0000-0000-0000-000000000001",
        farm_id="e7a3b110-0000-0000-0000-000000000001",
        sensor_type="SOIL_MOISTURE",
        reading_value=34.5,
        canonical_unit="%",
        reading_timestamp="2026-10-20T07:00:00Z",
        ingestion_batch_id="BATCH-TEST-001",
        battery_level_pct=88.5,
    )

    success = publisher.publish_telemetry(payload)
    assert success is True
    assert publisher.published_count == 1
    assert publisher.mock_sent_payloads[0]["routing_key"] == "telemetry.block.soil_moisture"


def test_simulator_cli_dry_run_execution() -> None:
    """Verifica la ejecución completa de extremo a extremo mediante el CLI en modo dry-run."""
    args = argparse.Namespace(
        mode="dry-run",
        farms=10,  # 30 sensores
        seed=42,
        days_disconnect=2,
        iterations=1,
        interval_seconds=0.0,
        rabbitmq_url=None,
    )
    exit_code = run_simulation(args)
    assert exit_code == 0
