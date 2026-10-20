"""AgroSense Café — Hardware Defect and Quality Anomaly Injector.

Implements injection of DEF-01 (Clock Drift), DEF-02 (Stuck Sensor),
DEF-03 (Cellular Disconnection / Delay RNF-01), and Depleted Battery (AGRO-15).
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

from agrosense_simulators.config import SimulatorConfig
from agrosense_simulators.sensor_generator import SensorNode

logger = logging.getLogger("agrosense.simulators.anomalies")


@dataclass
class AnomalyReport:
    """Resumen estructurado de las anomalías inyectadas en la red de sensores."""

    total_sensors: int = 0
    clock_drift_sensors_count: int = 0
    stuck_sensors_count: int = 0
    disconnected_sensors_count: int = 0
    low_battery_sensors_count: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)


class AnomalyInjector:
    """Aplica defectos de hardware y degradación de conectividad a nodos IoT."""

    def __init__(self, config: SimulatorConfig) -> None:
        self.config = config
        self.rng = random.Random(config.seed_value)
        self.report = AnomalyReport()

    def apply_defects(self, sensors: list[SensorNode]) -> AnomalyReport:
        """Aplica las anomalías configuradas sobre la lista de nodos sensores."""
        self.report = AnomalyReport(total_sensors=len(sensors))

        for sensor in sensors:
            # 1. DEF-01: Clock Drift (-2h a +12h)
            if self.rng.random() < self.config.seed_defect_clock_drift_rate:
                # Opciones de desfase entre -2h (-7200s) y +12h (+43200s)
                drift_hours = self.rng.choice([-2.0, -1.5, -0.75, 1.0, 2.5, 4.0, 8.0, 12.0])
                drift_seconds = int(drift_hours * 3600)
                sensor.clock_drift_seconds = drift_seconds
                self.report.clock_drift_sensors_count += 1
                self.report.details.append(
                    {
                        "defect": "DEF-01",
                        "sensor_id": sensor.sensor_id,
                        "farm_code": sensor.farm_code,
                        "sensor_type": sensor.sensor_type.value,
                        "drift_hours": drift_hours,
                        "drift_seconds": drift_seconds,
                    }
                )

            # 2. DEF-02: Stuck Sensor (variación < 0.5% en 24h)
            if self.rng.random() < self.config.seed_defect_stuck_rate:
                sensor.is_stuck = True
                # Valor fijo típico según el tipo de sensor
                if sensor.sensor_type.value == "TEMPERATURE":
                    sensor.stuck_value = round(self.rng.uniform(21.0, 24.0), 2)
                elif sensor.sensor_type.value == "SOIL_MOISTURE":
                    sensor.stuck_value = round(self.rng.uniform(32.0, 36.0), 2)
                else:  # PLUVIOMETER
                    sensor.stuck_value = 0.0  # Pluviómetro atascado reportando sequía

                self.report.stuck_sensors_count += 1
                self.report.details.append(
                    {
                        "defect": "DEF-02",
                        "sensor_id": sensor.sensor_id,
                        "farm_code": sensor.farm_code,
                        "sensor_type": sensor.sensor_type.value,
                        "stuck_value": sensor.stuck_value,
                    }
                )

            # 3. DEF-03: Desconexión / Retraso Celular (1 a 7 días de acumulación - RNF-01)
            if self.rng.random() < self.config.seed_defect_disconnect_rate:
                sensor.is_disconnected = True
                sensor.disconnect_days = self.rng.randint(
                    self.config.seed_min_disconnect_days,
                    self.config.seed_max_disconnect_days,
                )
                self.report.disconnected_sensors_count += 1
                self.report.details.append(
                    {
                        "defect": "DEF-03",
                        "sensor_id": sensor.sensor_id,
                        "farm_code": sensor.farm_code,
                        "sensor_type": sensor.sensor_type.value,
                        "disconnect_days": sensor.disconnect_days,
                    }
                )

            # 4. Batería Agotada (battery_level_pct < 15%)
            if self.rng.random() < self.config.seed_defect_low_battery_rate:
                sensor.battery_level_pct = round(self.rng.uniform(3.5, 14.8), 2)
                self.report.low_battery_sensors_count += 1
                self.report.details.append(
                    {
                        "defect": "LOW_BATTERY",
                        "sensor_id": sensor.sensor_id,
                        "farm_code": sensor.farm_code,
                        "sensor_type": sensor.sensor_type.value,
                        "battery_level_pct": sensor.battery_level_pct,
                    }
                )

        logger.info(
            "Anomalías inyectadas: DEF-01=%d, DEF-02=%d, DEF-03=%d, Batería Baja=%d "
            "(Total sensores: %d)",
            self.report.clock_drift_sensors_count,
            self.report.stuck_sensors_count,
            self.report.disconnected_sensors_count,
            self.report.low_battery_sensors_count,
            self.report.total_sensors,
        )

        return self.report
