"""FastConsumer de telemetría IoT (AGRO-16): consume ``telemetry.raw.queue`` con ACK manual.

Ejecución (contenedor co-ubicado con la API en la tarea Fargate, o servicio de Compose):

    python -m agrosense_api.workers.telemetry_consumer

* Declara la topología de forma idempotente (mismos argumentos que definitions.json).
* Usa ``FastTelemetryConsumerWorker`` para validar, deduplicar y ordenar por sensor.
* ACK sólo después de procesar (RNF-01). Payloads totalmente inválidos se rechazan sin
  requeue y el broker los enruta al DLX -> ``dead.letter.queue``; las lecturas inválidas de
  un bloque parcialmente válido se publican explícitamente en el DLX antes del ACK.
* Si el broker no está disponible al arrancar, reintenta con backoff en lugar de terminar.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import signal
from typing import Any

import aio_pika
from aio_pika.abc import AbstractExchange, AbstractIncomingMessage

from agrosense_api.core.config import get_settings
from agrosense_api.services.messaging import (
    FastTelemetryConsumerWorker,
    OrderedTelemetryEvent,
    RabbitMQTopologyConfig,
    declare_rabbitmq_topology,
)

logger = logging.getLogger("agrosense.telemetry_consumer")

RECONNECT_BACKOFF_SECONDS = (1, 2, 5, 10, 30)


def _log_event(event: OrderedTelemetryEvent) -> None:
    logger.debug(
        "Telemetry accepted sensor=%s farm=%s metric=%s observed_at=%s",
        event.sensor_id,
        event.farm_id,
        event.metric,
        event.observed_at.isoformat(),
    )


async def handle_message(
    worker: FastTelemetryConsumerWorker,
    message: AbstractIncomingMessage,
    dlx_exchange: AbstractExchange,
) -> str:
    """Procesa un mensaje y decide ACK / reject. Devuelve la acción aplicada."""
    dead_letters_before = len(worker.dead_letter_messages)
    try:
        report = worker.process_payload(message.body)
    except Exception:
        logger.exception("Unexpected error processing telemetry message %s", message.message_id)
        await message.reject(requeue=False)
        return "rejected"

    new_dead_letters = worker.dead_letter_messages[dead_letters_before:]
    if new_dead_letters and report.accepted_count == 0 and report.duplicate_count == 0:
        # Mensaje completamente inválido: el broker lo enruta al DLX configurado en la cola.
        await message.reject(requeue=False)
        return "rejected"

    for dead_letter in new_dead_letters:
        await dlx_exchange.publish(
            aio_pika.Message(
                body=json.dumps(dead_letter, default=str).encode("utf-8"),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                headers={"x-original-message-id": message.message_id or ""},
            ),
            routing_key=worker.topology.dlq_telemetry_routing_key,
        )
    await message.ack()
    return "acked"


async def _on_message(
    message: AbstractIncomingMessage,
    *,
    worker: FastTelemetryConsumerWorker,
    dlx_exchange: AbstractExchange,
) -> None:
    await handle_message(worker, message, dlx_exchange)


async def consume_forever(stop: asyncio.Event) -> None:
    settings = get_settings()
    broker_url = settings.rabbitmq_url
    if broker_url is None or not broker_url.get_secret_value():
        raise RuntimeError("AGROSENSE_RABBITMQ_URL must be configured for the telemetry consumer")

    topology_cfg = RabbitMQTopologyConfig()
    worker = FastTelemetryConsumerWorker(topology=topology_cfg, on_ordered_event=_log_event)
    attempt = 0
    while not stop.is_set():
        try:
            connection = await aio_pika.connect_robust(broker_url.get_secret_value())
        except (OSError, aio_pika.exceptions.AMQPException) as exc:
            delay = RECONNECT_BACKOFF_SECONDS[min(attempt, len(RECONNECT_BACKOFF_SECONDS) - 1)]
            attempt += 1
            logger.warning("RabbitMQ not reachable (%s); retrying in %ss", exc, delay)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            continue

        attempt = 0
        async with connection:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=settings.telemetry_consumer_prefetch)
            topology: dict[str, Any] = await declare_rabbitmq_topology(channel, topology_cfg)
            queue = topology["telemetry_queue"]
            logger.info("Consuming %s (manual ACK)", topology_cfg.telemetry_queue)

            on_message = functools.partial(
                _on_message, worker=worker, dlx_exchange=topology["dlx_exchange"]
            )
            consumer_tag = await queue.consume(on_message, no_ack=False)
            await stop.wait()
            await queue.cancel(consumer_tag)
    logger.info("Telemetry consumer stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    async def _run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):  # Windows
                pass
        await consume_forever(stop)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
