"""FastConsumer de telemetría IoT (AGRO-16): consume ``telemetry.raw.queue`` con ACK manual.

Ejecución (contenedor co-ubicado con la API en la tarea Fargate, o servicio de Compose):

    python -m agrosense_api.workers.telemetry_consumer

* Declara la topología de forma idempotente (mismos argumentos que definitions.json).
* Usa ``FastTelemetryConsumerWorker`` para validar, deduplicar y ordenar por sensor.
* Persiste las lecturas ordenadas en S3 Bronze (``raw/iot_telemetry/dt=<YYYY-MM-DD>/...``) y/o
  spool local antes de emitir el ACK manual (RNF-01, CA-01).
* ACK sólo después de procesar y persistir (RNF-01). Payloads totalmente inválidos se rechazan sin
  requeue y el broker los enruta al DLX -> ``dead.letter.queue``; las lecturas inválidas de
  un bloque parcialmente válido se publican explícitamente en el DLX antes del ACK.
* Si el broker no está disponible al arrancar, reintenta con backoff en lugar de terminar.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import signal
import tempfile
from collections import defaultdict
from pathlib import Path
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


class TelemetryPersistenceSink:
    """Persiste eventos ordenados de telemetría IoT en S3 Bronze y spool local (CA-01, RNF-01)."""

    def __init__(
        self,
        *,
        spool_dir: str | Path | None = None,
        s3_bucket: str | None = None,
        s3_client: Any | None = None,
    ) -> None:
        default_spool = os.getenv(
            "AGROSENSE_TELEMETRY_SPOOL_DIR",
            str(Path(tempfile.gettempdir()) / "agrosense-telemetry-spool"),
        )
        self.spool_dir = Path(spool_dir or default_spool)
        self.s3_bucket = (
            s3_bucket
            or os.getenv("AGROSENSE_S3_BUCKET_RAW")
            or os.getenv("S3_BUCKET_BRONZE")
            or os.getenv("S3_BUCKET")
            or ""
        ).strip()
        self._s3_client = s3_client
        self.persisted_batches: list[dict[str, Any]] = []

    def _get_s3_client(self) -> Any | None:
        if self._s3_client is not None:
            return self._s3_client
        if not self.s3_bucket:
            return None
        try:
            import boto3

            self._s3_client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-2"))
            return self._s3_client
        except Exception as exc:  # pragma: no cover
            logger.warning("Unable to initialize S3 client for telemetry persistence: %s", exc)
            return None

    def persist_batch(
        self,
        ordered_by_sensor: dict[str, list[OrderedTelemetryEvent]],
        *,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        """Escribe lecturas ordenadas por fecha y sensor en disco local y S3 Bronze."""
        by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        total_events = 0
        for events in ordered_by_sensor.values():
            for ev in events:
                dt_str = ev.observed_at.strftime("%Y-%m-%d")
                by_date[dt_str].append(ev.to_dict())
                total_events += 1

        if total_events == 0:
            return {"persisted_count": 0, "local_files": [], "s3_uris": []}

        safe_batch = (batch_id or "batch").replace("/", "_").replace(":", "_")
        local_files: list[str] = []
        s3_uris: list[str] = []
        s3 = self._get_s3_client()

        for dt_str, records in sorted(by_date.items()):
            part_dir = self.spool_dir / "raw" / "iot_telemetry" / f"dt={dt_str}"
            part_dir.mkdir(parents=True, exist_ok=True)
            local_file = part_dir / f"telemetry_{safe_batch}.json"
            payload_doc = {
                "source": "IOT_RABBITMQ_FAST_CONSUMER",
                "ingestion_date": dt_str,
                "batch_id": safe_batch,
                "record_count": len(records),
                "readings": records,
            }
            encoded = json.dumps(payload_doc, indent=2).encode("utf-8")
            local_file.write_bytes(encoded)
            local_files.append(str(local_file))

            if s3 is not None and self.s3_bucket:
                s3_key = f"raw/iot_telemetry/dt={dt_str}/telemetry_{safe_batch}.json"
                try:
                    s3.put_object(
                        Bucket=self.s3_bucket,
                        Key=s3_key,
                        Body=encoded,
                        ContentType="application/json",
                    )
                    s3_uris.append(f"s3://{self.s3_bucket}/{s3_key}")
                except Exception as exc:
                    logger.warning("Failed to upload telemetry batch to S3 (%s): %s", s3_key, exc)

        summary = {
            "persisted_count": total_events,
            "local_files": local_files,
            "s3_uris": s3_uris,
        }
        self.persisted_batches.append(summary)
        return summary


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
    *,
    sink: TelemetryPersistenceSink | None = None,
) -> str:
    """Procesa un mensaje, persiste lecturas ordenadas y decide ACK / reject."""
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

    if report.accepted_count > 0:
        active_sink = (
            sink or getattr(worker, "persistence_sink", None) or TelemetryPersistenceSink()
        )
        active_sink.persist_batch(
            report.ordered_by_sensor,
            batch_id=message.message_id or "msg",
        )

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
    sink: TelemetryPersistenceSink,
) -> None:
    await handle_message(worker, message, dlx_exchange, sink=sink)


async def consume_forever(stop: asyncio.Event) -> None:
    settings = get_settings()
    broker_url = settings.rabbitmq_url
    if broker_url is None or not broker_url.get_secret_value():
        raise RuntimeError("AGROSENSE_RABBITMQ_URL must be configured for the telemetry consumer")

    topology_cfg = RabbitMQTopologyConfig()
    sink = TelemetryPersistenceSink()
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
                _on_message,
                worker=worker,
                dlx_exchange=topology["dlx_exchange"],
                sink=sink,
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
