"""AgroSense Café — Physical Sensor Generator & Chronological Buffers.

Implements realistic physical time-series generation for coffee crops and
maintains chronological buffers for burst/block transmissions (AGRO-15, RNF-01).
"""

from __future__ import annotations

import math
import random
import uuid
from collections import deque
from datetime import UTC, datetime, timedelta

from agrosense_simulators.config import (
    AGROSENSE_NAMESPACE,
    SENSOR_ROUTING_KEY_MAP,
    SENSOR_UNIT_MAP,
    SensorType,
    TelemetryPayload,
)


class SensorNode:
    """Representa un sensor IoT físico instalado en una finca piloto."""

    def __init__(
        self,
        farm_code: str,
        farm_id: str,
        sensor_type: SensorType,
        sensor_id: str,
        firmware_version: str = "v1.0.0",
        seed: int = 42,
    ) -> None:
        self.farm_code = farm_code
        self.farm_id = farm_id
        self.sensor_type = sensor_type
        self.sensor_id = sensor_id
        self.canonical_unit = SENSOR_UNIT_MAP[sensor_type]
        self.routing_key = SENSOR_ROUTING_KEY_MAP[sensor_type]
        self.firmware_version = firmware_version
        self.rng = random.Random(seed)

        # Estado físico interno
        self.battery_level_pct: float = round(self.rng.uniform(92.0, 99.5), 2)
        self.battery_drain_rate: float = 0.005  # Descarga por intervalo de 10 min

        # Estado para variables continuas
        self.current_soil_moisture: float = round(self.rng.uniform(28.0, 42.0), 2)
        self.rain_event_active: bool = False
        self.rain_event_remaining_intervals: int = 0
        self.rain_event_intensity: float = 0.0

        # Inyección de defectos de hardware
        self.clock_drift_seconds: int = 0  # DEF-01: -7200s (-2h) a +43200s (+12h)
        self.is_stuck: bool = False  # DEF-02: Sensor congelado
        self.stuck_value: float | None = None
        self.is_disconnected: bool = False  # DEF-03: Desconexión celular
        self.disconnect_days: int = 0

        # Búfer cronológico para retención local por desconexión celular (RNF-01)
        self.chronological_buffer: deque[TelemetryPayload] = deque()

    def generate_physical_value(self, current_dt: datetime) -> float:
        """Calcula el valor físico nominal realista según la física agronómica."""
        # Si tiene DEF-02 (stuck sensor), reporta lectura congelada (<0.5% variación)
        if self.is_stuck and self.stuck_value is not None:
            # Micro-jitter menor al 0.1% para mantener varianza < 0.5%
            jitter = self.rng.uniform(-0.002, 0.002) * self.stuck_value
            return round(self.stuck_value + jitter, 2)

        if self.sensor_type == SensorType.TEMPERATURE:
            # Variación sinusoidal diurna entre 15 °C y 28 °C
            # Promedio: 21.5 °C, Amplitud: 6.5 °C
            # Pico de temperatura a las 14:00, mínima a las 02:00-05:00
            hour_float = current_dt.hour + (current_dt.minute / 60.0)
            # Fase: pico a las 14.0 -> sin((14 - 8) * pi / 12) = sin(pi/2) = 1.0
            solar_phase = (hour_float - 8.0) * math.pi / 12.0
            diurnal_temp = 21.5 + 6.5 * math.sin(solar_phase)
            jitter = self.rng.uniform(-0.35, 0.35)
            val = round(diurnal_temp + jitter, 1)
            # Acotado estrictamente entre 15.0 °C y 28.0 °C
            return max(15.0, min(28.0, val))

        if self.sensor_type == SensorType.SOIL_MOISTURE:
            # Humedad del suelo continua entre 15% y 55%
            # Evolución suave tipo random walk con retorno al rango central
            step = self.rng.uniform(-0.3, 0.3)
            # Tendencia de secado si está alto, o absorción si llueve
            if self.current_soil_moisture > 45.0:
                step -= 0.1
            elif self.current_soil_moisture < 20.0:
                step += 0.1
            self.current_soil_moisture = round(self.current_soil_moisture + step, 1)
            self.current_soil_moisture = max(15.0, min(55.0, self.current_soil_moisture))
            return self.current_soil_moisture

        if self.sensor_type == SensorType.PLUVIOMETER:
            # Eventos esporádicos de precipitación entre 0 mm y 45 mm
            if self.rain_event_active:
                self.rain_event_remaining_intervals -= 1
                if self.rain_event_remaining_intervals <= 0:
                    self.rain_event_active = False
                jitter = self.rng.uniform(-1.5, 1.5)
                val = max(0.0, min(45.0, round(self.rain_event_intensity + jitter, 1)))
                return val
            # Probabilidad de iniciar un evento de lluvia (~2.5% de los intervalos de 10 min)
            if self.rng.random() < 0.025:
                self.rain_event_active = True
                self.rain_event_remaining_intervals = self.rng.randint(2, 6)
                self.rain_event_intensity = round(self.rng.uniform(2.0, 42.0), 1)
                return self.rain_event_intensity
            return 0.0

        return 0.0

    def step_reading(
        self,
        nominal_dt: datetime,
        batch_id: str,
    ) -> TelemetryPayload:
        """Genera una lectura para el timestamp nominal aplicando defectos de hardware."""
        # 1. Aplicar DEF-01: Clock Drift sobre el timestamp de dispositivo
        effective_dt = nominal_dt
        if self.clock_drift_seconds != 0:
            effective_dt = nominal_dt + timedelta(seconds=self.clock_drift_seconds)

        # Formato ISO-8601 UTC estricto con 'Z'
        if effective_dt.tzinfo is None:
            effective_dt = effective_dt.replace(tzinfo=UTC)
        reading_timestamp_str = effective_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 2. Generar valor físico
        reading_value = self.generate_physical_value(effective_dt)

        # 3. Degradar batería
        self.battery_level_pct = max(
            0.0,
            round(self.battery_level_pct - self.battery_drain_rate, 2),
        )

        payload = TelemetryPayload(
            sensor_id=self.sensor_id,
            farm_id=self.farm_id,
            sensor_type=self.sensor_type.value,  # type: ignore[arg-type]
            reading_value=reading_value,
            canonical_unit=self.canonical_unit.value,  # type: ignore[arg-type]
            reading_timestamp=reading_timestamp_str,
            ingestion_batch_id=batch_id,
            battery_level_pct=self.battery_level_pct,
            firmware_version=self.firmware_version,
        )

        return payload

    def buffer_reading(self, payload: TelemetryPayload) -> None:
        """Almacena una lectura en el búfer cronológico local durante desconexión."""
        self.chronological_buffer.append(payload)

    def flush_burst_buffer(self, burst_batch_id: str | None = None) -> list[TelemetryPayload]:
        """Extrae el búfer cronológico en orden estricto (antiguo a nuevo) para envío en ráfaga.

        Todas las lecturas de la ráfaga comparten el mismo `ingestion_batch_id` (RNF-01).
        """
        burst_records: list[TelemetryPayload] = []
        effective_batch_id = (
            burst_batch_id
            or f"BATCH-BURST-{self.sensor_id[:8]}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        )

        while self.chronological_buffer:
            reading = self.chronological_buffer.popleft()
            # Asignar el ingestion_batch_id unificado de la ráfaga de reconexión
            updated_reading = TelemetryPayload(
                sensor_id=reading.sensor_id,
                farm_id=reading.farm_id,
                sensor_type=reading.sensor_type,
                reading_value=reading.reading_value,
                canonical_unit=reading.canonical_unit,
                reading_timestamp=reading.reading_timestamp,
                ingestion_batch_id=effective_batch_id,
                battery_level_pct=reading.battery_level_pct,
                firmware_version=reading.firmware_version,
            )
            burst_records.append(updated_reading)

        return burst_records


class SensorNetwork:
    """Inicializa deterministamente la red completa de 900 sensores en 300 fincas."""

    def __init__(
        self,
        farms_count: int = 300,
        firmware_version: str = "v1.0.0",
        seed: int = 42,
    ) -> None:
        self.farms_count = farms_count
        self.firmware_version = firmware_version
        self.seed = seed
        self.sensors: list[SensorNode] = []
        self._initialize_sensors()

    def _initialize_sensors(self) -> None:
        """Crea 3 sensores por finca piloto con identificadores deterministas UUIDv5."""
        sensor_types = [
            SensorType.SOIL_MOISTURE,
            SensorType.TEMPERATURE,
            SensorType.PLUVIOMETER,
        ]

        sensor_counter = 0
        for farm_idx in range(1, self.farms_count + 1):
            farm_code = f"FIN-{farm_idx:04d}"
            farm_id = str(uuid.uuid5(AGROSENSE_NAMESPACE, farm_code))

            for stype in sensor_types:
                sensor_counter += 1
                # Clave natural determinista: farm_code + ':' + sensor_type
                natural_key = f"{farm_code}:{stype.value}"
                sensor_id = str(uuid.uuid5(AGROSENSE_NAMESPACE, natural_key))
                sensor_seed = self.seed + (farm_idx * 10) + stype_to_seed_offset(stype)

                node = SensorNode(
                    farm_code=farm_code,
                    farm_id=farm_id,
                    sensor_type=stype,
                    sensor_id=sensor_id,
                    firmware_version=self.firmware_version,
                    seed=sensor_seed,
                )
                self.sensors.append(node)

    def get_sensors_by_type(self, sensor_type: SensorType) -> list[SensorNode]:
        """Filtra nodos por tipo de sensor."""
        return [s for s in self.sensors if s.sensor_type == sensor_type]

    @property
    def total_sensors(self) -> int:
        return len(self.sensors)


def stype_to_seed_offset(stype: SensorType) -> int:
    """Offset determinista por tipo de sensor."""
    if stype == SensorType.SOIL_MOISTURE:
        return 1
    if stype == SensorType.TEMPERATURE:
        return 2
    return 3
