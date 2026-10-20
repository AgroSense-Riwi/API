"""AgroSense Café — Resilient AMQP 0-9-1 Publisher for RabbitMQ.

Publishes telemetry payloads to the topic exchange 'agrosense.ingest.exchange'
with durable delivery (delivery_mode=2) and exponential backoff retries (AGRO-15).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import pika
from pika.exceptions import AMQPConnectionError, AMQPError

from agrosense_simulators.config import SENSOR_ROUTING_KEY_MAP, SensorType, TelemetryPayload

logger = logging.getLogger("agrosense.simulators.amqp")


class RabbitMQPublisher:
    """Cliente AMQP resiliente con soporte de reconexión automática y retroceso exponencial."""

    def __init__(
        self,
        rabbitmq_url: str,
        exchange_name: str = "agrosense.ingest.exchange",
        max_retries: int = 3,
        retry_delay_seconds: float = 1.0,
        mock_mode: bool = False,
    ) -> None:
        self.rabbitmq_url = rabbitmq_url
        self.exchange_name = exchange_name
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.mock_mode = mock_mode

        self._connection: pika.BlockingConnection | None = None
        self._channel: Any | None = None
        self.published_count: int = 0
        self.mock_sent_payloads: list[dict[str, Any]] = []

    def connect(self) -> bool:
        """Establece conexión y canal durable con RabbitMQ utilizando reintentos exponenciales."""
        if self.mock_mode:
            logger.info("RabbitMQPublisher inicializado en modo MOCK (sin conexión de red).")
            return True

        attempt = 1
        current_delay = self.retry_delay_seconds

        while attempt <= self.max_retries:
            try:
                logger.info(
                    "Conectando a RabbitMQ (intento %d/%d): %s",
                    attempt,
                    self.max_retries,
                    self._mask_url(self.rabbitmq_url),
                )
                params = pika.URLParameters(self.rabbitmq_url)
                # Configurar timeout y heartbeat razonables
                params.heartbeat = 60
                params.blocked_connection_timeout = 300

                self._connection = pika.BlockingConnection(params)
                self._channel = self._connection.channel()

                # Declarar Topic Exchange durable
                self._channel.exchange_declare(
                    exchange=self.exchange_name,
                    exchange_type="topic",
                    durable=True,
                    auto_delete=False,
                )

                logger.info(
                    "Conexión exitosa a RabbitMQ y Exchange '%s' verificado.",
                    self.exchange_name,
                )
                return True
            except (AMQPConnectionError, AMQPError, OSError) as err:
                if attempt < self.max_retries:
                    logger.warning(
                        "Fallo de conexión AMQP en intento %d/%d: %s. Reintentando en %.1fs...",
                        attempt,
                        self.max_retries,
                        err,
                        current_delay,
                    )
                    time.sleep(current_delay)
                    current_delay *= 2.0
                else:
                    logger.warning(
                        "Fallo de conexión AMQP en intento final %d/%d: %s.",
                        attempt,
                        self.max_retries,
                        err,
                    )
                attempt += 1

        logger.error(
            "No fue posible conectar a RabbitMQ tras %d intentos.",
            self.max_retries,
        )
        return False

    def is_connected(self) -> bool:
        """Indica si el canal y conexión están abiertos."""
        if self.mock_mode:
            return True
        return bool(
            self._connection
            and self._connection.is_open
            and self._channel
            and self._channel.is_open
        )

    def publish_telemetry(self, payload: TelemetryPayload) -> bool:
        """Publica un payload individual de telemetría hacia el Exchange topic correspondiente."""
        # Obtener routing key según tipo de sensor
        stype = SensorType(payload.sensor_type)
        routing_key = SENSOR_ROUTING_KEY_MAP.get(stype, "telemetry.block.unknown")

        body_dict = payload.model_dump()
        body_bytes = json.dumps(body_dict).encode("utf-8")

        if self.mock_mode:
            self.mock_sent_payloads.append({"routing_key": routing_key, "body": body_dict})
            self.published_count += 1
            return True

        # Asegurar conexión activa
        if not self.is_connected() and not self.connect():
            logger.error("No se puede publicar telemetría: conexión RabbitMQ inactiva.")
            return False

        if self._channel is None:
            logger.error("Canal AMQP no disponible para publicación.")
            return False

        try:
            properties = pika.BasicProperties(
                delivery_mode=2,  # Mensaje persistente en disco (RNF-01)
                content_type="application/json",
                content_encoding="utf-8",
                app_id="agrosense-simulators",
                headers={
                    "sensor_type": payload.sensor_type,
                    "farm_id": payload.farm_id,
                    "batch_id": payload.ingestion_batch_id,
                },
            )

            self._channel.basic_publish(
                exchange=self.exchange_name,
                routing_key=routing_key,
                body=body_bytes,
                properties=properties,
            )
            self.published_count += 1
            return True
        except (AMQPError, OSError) as err:
            logger.warning("Error al publicar mensaje AMQP: %s. Intentando reconectar...", err)
            if self.connect() and self._channel is not None:
                self._channel.basic_publish(
                    exchange=self.exchange_name,
                    routing_key=routing_key,
                    body=body_bytes,
                    properties=properties,
                )
                self.published_count += 1
                return True
            return False

    def publish_batch(self, payloads: list[TelemetryPayload]) -> int:
        """Publica una ráfaga o bloque de telemetrías compartiendo un lote (RNF-01).

        Returns:
            int: Cantidad de mensajes publicados exitosamente.
        """
        success = 0
        for payload in payloads:
            if self.publish_telemetry(payload):
                success += 1
        return success

    def close(self) -> None:
        """Cierra de manera ordenada la conexión AMQP."""
        if self._connection and self._connection.is_open:
            try:
                self._connection.close()
                logger.info("Conexión RabbitMQ cerrada limpiamente.")
            except Exception as err:
                logger.debug("Error cerrando conexión RabbitMQ: %s", err)

    def _mask_url(self, url: str) -> str:
        """Enmascara contraseñas en URLs de conexión para logs seguros."""
        try:
            import re

            return re.sub(r":([^@:]+)@", ":****@", url)
        except Exception:
            return url
