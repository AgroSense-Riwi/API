"""AgroSense Café — Simulator Configuration and Telemetry Schemas.

Defines Pydantic models for configuration reading (.env) and the strict
telemetry JSON payload contract for AGRO-15.
"""

from __future__ import annotations

import os
import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Namespace UUIDv5 determinista estándar del proyecto AgroSense
AGROSENSE_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


class SensorType(StrEnum):
    """Tipos de sensores IoT soportados en fincas cafeteras piloto."""

    SOIL_MOISTURE = "SOIL_MOISTURE"
    TEMPERATURE = "TEMPERATURE"
    PLUVIOMETER = "PLUVIOMETER"


class CanonicalUnit(StrEnum):
    """Unidades canónicas de medida por tipo de sensor físico."""

    PERCENT = "%"
    CELSIUS = "CELSIUS"
    MM = "MM"


SENSOR_UNIT_MAP: dict[SensorType, CanonicalUnit] = {
    SensorType.SOIL_MOISTURE: CanonicalUnit.PERCENT,
    SensorType.TEMPERATURE: CanonicalUnit.CELSIUS,
    SensorType.PLUVIOMETER: CanonicalUnit.MM,
}

SENSOR_ROUTING_KEY_MAP: dict[SensorType, str] = {
    SensorType.SOIL_MOISTURE: "telemetry.block.soil_moisture",
    SensorType.TEMPERATURE: "telemetry.block.temperature",
    SensorType.PLUVIOMETER: "telemetry.block.pluviometer",
}


class TelemetryPayload(BaseModel):
    """Estructura estricta del Payload JSON de Telemetría IoT (AGRO-15)."""

    sensor_id: str = Field(
        ...,
        description="Identificador determinista UUIDv5 del sensor IoT.",
    )
    farm_id: str = Field(
        ...,
        description="Identificador determinista UUIDv5 de la finca asociada.",
    )
    sensor_type: Literal["SOIL_MOISTURE", "TEMPERATURE", "PLUVIOMETER"] = Field(
        ...,
        description="Tipo funcional de sensor IoT.",
    )
    reading_value: float = Field(
        ...,
        description="Valor físico medido por el dispositivo.",
    )
    canonical_unit: Literal["%", "CELSIUS", "MM"] = Field(
        ...,
        description="Unidad canónica correspondiente al sensor.",
    )
    reading_timestamp: str = Field(
        ...,
        description="Marca temporal ISO-8601 UTC en que el hardware capturó la lectura.",
    )
    ingestion_batch_id: str = Field(
        ...,
        description="Identificador del lote de ingesta para streaming o ráfaga (burst).",
    )
    battery_level_pct: float = Field(
        ...,
        description="Porcentaje restante de batería del nodo IoT (0.0 a 100.0).",
    )
    firmware_version: str = Field(
        default="v1.0.0",
        description="Versión del firmware instalada en el dispositivo.",
    )

    @field_validator("reading_timestamp")
    @classmethod
    def validate_timestamp_format(cls, value: str) -> str:
        """Asegura que el timestamp cumpla con formato ISO-8601 UTC."""
        if not value:
            msg = "reading_timestamp cannot be empty"
            raise ValueError(msg)
        # Permite formato terminado en Z o con offset UTC
        if not (value.endswith("Z") or "+00:00" in value):
            msg = f"reading_timestamp must be in UTC (end with Z or +00:00), got: {value}"
            raise ValueError(msg)
        return value

    @field_validator("battery_level_pct")
    @classmethod
    def validate_battery_range(cls, value: float) -> float:
        """Valida que el nivel de batería esté acotado entre 0.0 y 100.0."""
        if not 0.0 <= value <= 100.0:
            msg = f"battery_level_pct must be between 0.0 and 100.0, got: {value}"
            raise ValueError(msg)
        return round(value, 2)


class SimulatorConfig(BaseSettings):
    """Configuración del servicio de simulación IoT cargada desde entorno/.env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Conexión al Broker RabbitMQ (AMQP 0-9-1)
    # Lee RABBITMQ_URL o AGROSENSE_RABBITMQ_URL
    rabbitmq_url: str = Field(
        default_factory=lambda: (
            os.getenv("RABBITMQ_URL")
            or os.getenv("AGROSENSE_RABBITMQ_URL")
            or "amqp://agrosense_mq:mq_secure_pass_2026@localhost:5672//agrosense"
        ),
        alias="RABBITMQ_URL",
    )
    exchange_name: str = Field(
        default="agrosense.ingest.exchange",
        alias="RABBITMQ_EXCHANGE",
    )

    # Topología física de la simulación
    farms_count: int = Field(default=300, alias="SIMULATOR_FARMS_COUNT")
    sensors_per_farm: int = Field(default=3, alias="SIMULATOR_SENSORS_PER_FARM")
    reading_interval_minutes: int = Field(default=10, alias="SIMULATOR_INTERVAL_MINUTES")

    # Semilla determinista PRNG
    seed_value: int = Field(default=42, alias="SEED_VALUE")

    # Inyección de anomalías y defectos (Sección 5.3)
    seed_defect_disconnect_rate: float = Field(
        default=0.05,
        alias="SEED_DEFECT_DISCONNECT_RATE",
    )
    seed_max_disconnect_days: int = Field(
        default=7,
        alias="SEED_MAX_DISCONNECT_DAYS",
    )
    seed_min_disconnect_days: int = Field(
        default=1,
        alias="SEED_MIN_DISCONNECT_DAYS",
    )
    seed_defect_clock_drift_rate: float = Field(
        default=0.05,
        alias="SEED_DEFECT_CLOCK_DRIFT_RATE",
    )
    seed_defect_stuck_rate: float = Field(
        default=0.03,
        alias="SEED_DEFECT_STUCK_RATE",
    )
    seed_defect_low_battery_rate: float = Field(
        default=0.04,
        alias="SEED_DEFECT_LOW_BATTERY_RATE",
    )

    # Versión de Firmware
    firmware_version: str = Field(default="v1.0.0", alias="SIMULATOR_FIRMWARE_VERSION")

    # Modo de ejecución: 'dev', 'prod', 'burst', 'dry-run'
    mode: str = Field(default="dev", alias="SIMULATOR_MODE")
