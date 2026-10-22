"""AgroSense Café — RabbitMQ Asynchronous Ingestion, Ordering & DLQ (AGRO-16 / SCRUM-35).

Implements:
- SCRUM-147: RabbitMQ Exchange (`agrosense.ingest.exchange`), Queue Topology
  (`telemetry.raw.queue`, `climate.ingest.queue`), and DLX (`agrosense.dlx.exchange`).
- SCRUM-148 (CA-01 / RN-01): FastConsumer Worker with strict per-sensor chronological
  ordering for 3-day to 7-day accumulated telemetry bursts without message loss.
- SCRUM-149: Idempotent consumer verification and deterministic message deduplication logic.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import aio_pika

from agrosense_api.core.config import get_settings
from agrosense_api.schemas.telemetry import TelemetryCreate

logger = logging.getLogger(__name__)


class MessageBrokerUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class RabbitMQTopologyConfig:
    """Declarative RabbitMQ exchange, queue, and DLX topology (SCRUM-147)."""

    ingest_exchange: str = "agrosense.ingest.exchange"
    dlx_exchange: str = "agrosense.dlx.exchange"
    telemetry_queue: str = "telemetry.raw.queue"
    climate_queue: str = "climate.ingest.queue"
    dead_letter_queue: str = "dead.letter.queue"
    telemetry_routing_key: str = "telemetry.block.#"
    climate_routing_key: str = "climate.daily.#"
    dlq_telemetry_routing_key: str = "dlq.telemetry"
    dlq_climate_routing_key: str = "dlq.climate"
    message_ttl_ms: int = 604_800_000  # 7 days (RN-01)

    def to_definitions_dict(self) -> dict[str, Any]:
        return {
            "exchanges": [
                {"name": self.ingest_exchange, "type": "topic", "durable": True},
                {"name": self.dlx_exchange, "type": "direct", "durable": True},
            ],
            "queues": [
                {
                    "name": self.telemetry_queue,
                    "durable": True,
                    "arguments": {
                        "x-dead-letter-exchange": self.dlx_exchange,
                        "x-dead-letter-routing-key": self.dlq_telemetry_routing_key,
                        "x-message-ttl": self.message_ttl_ms,
                    },
                },
                {
                    "name": self.climate_queue,
                    "durable": True,
                    "arguments": {
                        "x-dead-letter-exchange": self.dlx_exchange,
                        "x-dead-letter-routing-key": self.dlq_climate_routing_key,
                    },
                },
                {"name": self.dead_letter_queue, "durable": True},
            ],
        }


async def declare_rabbitmq_topology(
    channel: aio_pika.abc.AbstractChannel,
    topology: RabbitMQTopologyConfig | None = None,
) -> dict[str, Any]:
    """Idempotently declare exchanges, durable queues, DLX, and bindings on RabbitMQ (SCRUM-147)."""
    cfg = topology or RabbitMQTopologyConfig()

    ingest_ex = await channel.declare_exchange(
        cfg.ingest_exchange, aio_pika.ExchangeType.TOPIC, durable=True
    )
    dlx_ex = await channel.declare_exchange(
        cfg.dlx_exchange, aio_pika.ExchangeType.DIRECT, durable=True
    )

    dlq = await channel.declare_queue(cfg.dead_letter_queue, durable=True)
    await dlq.bind(dlx_ex, routing_key=cfg.dlq_telemetry_routing_key)
    await dlq.bind(dlx_ex, routing_key=cfg.dlq_climate_routing_key)

    telemetry_q = await channel.declare_queue(
        cfg.telemetry_queue,
        durable=True,
        arguments={
            "x-dead-letter-exchange": cfg.dlx_exchange,
            "x-dead-letter-routing-key": cfg.dlq_telemetry_routing_key,
            "x-message-ttl": cfg.message_ttl_ms,
        },
    )
    await telemetry_q.bind(ingest_ex, routing_key=cfg.telemetry_routing_key)

    climate_q = await channel.declare_queue(
        cfg.climate_queue,
        durable=True,
        arguments={
            "x-dead-letter-exchange": cfg.dlx_exchange,
            "x-dead-letter-routing-key": cfg.dlq_climate_routing_key,
        },
    )
    await climate_q.bind(ingest_ex, routing_key=cfg.climate_routing_key)

    return {
        "ingest_exchange": ingest_ex,
        "dlx_exchange": dlx_ex,
        "telemetry_queue": telemetry_q,
        "climate_queue": climate_q,
        "dead_letter_queue": dlq,
    }


async def publish_telemetry(event_id: UUID, telemetry: TelemetryCreate) -> None:
    settings = get_settings()
    broker_url = settings.rabbitmq_url
    if broker_url is None or not broker_url.get_secret_value():
        raise MessageBrokerUnavailable("AGROSENSE_RABBITMQ_URL is not configured")

    try:
        connection = await aio_pika.connect_robust(broker_url.get_secret_value())
        async with connection:
            channel = await connection.channel(publisher_confirms=True)
            queue = await channel.declare_queue(settings.telemetry_queue, durable=True)
            message = aio_pika.Message(
                body=json.dumps(
                    {"event_id": str(event_id), **telemetry.model_dump(mode="json")}
                ).encode("utf-8"),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=str(event_id),
            )
            await channel.default_exchange.publish(message, routing_key=queue.name)
    except (TimeoutError, aio_pika.exceptions.AMQPException, OSError) as exc:
        logger.exception("Unable to publish telemetry event %s", event_id)
        raise MessageBrokerUnavailable("Telemetry broker is unavailable") from exc


@dataclass(frozen=True, slots=True)
class OrderedTelemetryEvent:
    """Normalized telemetry event ordered chronologically per sensor partition (SCRUM-148)."""

    idempotency_key: str
    event_id: str
    farm_id: str
    sensor_id: str
    metric: str
    value: float
    unit: str
    observed_at: datetime


class IdempotencyDeduplicator:
    """Bounded LRU idempotency registry preventing duplicate telemetry processing (SCRUM-149)."""

    def __init__(self, max_entries: int = 50_000) -> None:
        self.max_entries = max_entries
        self._seen: OrderedDict[str, datetime] = OrderedDict()

    @staticmethod
    def compute_key(payload: dict[str, Any]) -> str:
        """Compute deterministic idempotency key from event_id or (sensor_id, metric, timestamp)."""
        explicit_id = payload.get("event_id") or payload.get("message_id")
        sensor_id = str(payload.get("sensor_id", ""))
        metric = str(payload.get("metric") or payload.get("sensor_type", ""))
        ts = str(payload.get("observed_at") or payload.get("reading_timestamp", ""))
        raw = f"{explicit_id or ''}|{sensor_id}|{metric}|{ts}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def check_and_record(self, key: str, recorded_at: datetime | None = None) -> bool:
        """Return True if key is new (and record it); False if duplicate."""
        if key in self._seen:
            self._seen.move_to_end(key)
            return False
        self._seen[key] = recorded_at or datetime.now(UTC)
        if len(self._seen) > self.max_entries:
            self._seen.popitem(last=False)
        return True

    def __len__(self) -> int:
        return len(self._seen)


@dataclass
class ConsumerBatchReport:
    """Metrics produced after consuming a single message or a multi-day burst (CA-01)."""

    accepted_count: int = 0
    duplicate_count: int = 0
    dlq_rejected_count: int = 0
    ordered_by_sensor: dict[str, list[OrderedTelemetryEvent]] = field(default_factory=dict)


class FastTelemetryConsumerWorker:
    """FastConsumer worker enforcing per-sensor ordering & deduplication (SCRUM-148, SCRUM-149)."""

    def __init__(
        self,
        *,
        topology: RabbitMQTopologyConfig | None = None,
        deduplicator: IdempotencyDeduplicator | None = None,
        on_ordered_event: Callable[[OrderedTelemetryEvent], None] | None = None,
    ) -> None:
        self.topology = topology or RabbitMQTopologyConfig()
        self.deduplicator = deduplicator or IdempotencyDeduplicator()
        self.on_ordered_event = on_ordered_event
        self.processed_by_sensor: dict[str, list[OrderedTelemetryEvent]] = defaultdict(list)
        self.dead_letter_messages: list[dict[str, Any]] = []
        self.duplicate_keys: list[str] = []

    @staticmethod
    def _parse_timestamp(raw_ts: Any) -> datetime:
        if isinstance(raw_ts, datetime):
            ts = raw_ts
        elif isinstance(raw_ts, str) and raw_ts.strip():
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        else:
            raise ValueError("Missing or invalid timestamp in telemetry payload")
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC)

    def _validate_and_normalize(self, item: dict[str, Any]) -> OrderedTelemetryEvent:
        if not isinstance(item, dict):
            raise ValueError("Telemetry item must be a JSON object")

        sensor_id = str(item.get("sensor_id") or "").strip()
        farm_id = str(item.get("farm_id") or "").strip()
        metric = str(item.get("metric") or item.get("sensor_type") or "").strip()
        unit = str(item.get("unit") or "%").strip()
        raw_val = item.get("value", item.get("reading_value"))

        if not sensor_id or not farm_id or not metric or raw_val is None:
            raise ValueError(
                "Missing required telemetry fields (sensor_id, farm_id, metric, value)"
            )

        value = float(raw_val)
        raw_ts = item.get("observed_at") or item.get("reading_timestamp")
        observed_at = self._parse_timestamp(raw_ts)
        key = self.deduplicator.compute_key(
            {
                "event_id": item.get("event_id"),
                "sensor_id": sensor_id,
                "metric": metric,
                "observed_at": observed_at.isoformat(),
            }
        )
        event_id = str(item.get("event_id") or key[:36])

        return OrderedTelemetryEvent(
            idempotency_key=key,
            event_id=event_id,
            farm_id=farm_id,
            sensor_id=sensor_id,
            metric=metric,
            value=value,
            unit=unit,
            observed_at=observed_at,
        )

    def process_payload(
        self, raw_payload: str | bytes | dict[str, Any] | list[Any]
    ) -> ConsumerBatchReport:
        """Process a single telemetry payload or a 3-day/7-day accumulated burst (CA-01, RN-01).

        - Corrupted JSON or invalid readings are routed to `dead_letter_messages` (DLX -> DLQ).
        - Duplicate messages are filtered out via `IdempotencyDeduplicator` (SCRUM-149).
        - Valid messages are partitioned by `sensor_id` and sorted chronologically (SCRUM-148).
        """
        report = ConsumerBatchReport()

        try:
            if isinstance(raw_payload, (str, bytes)):
                decoded = json.loads(raw_payload)
            else:
                decoded = raw_payload
        except Exception as exc:
            self.dead_letter_messages.append(
                {
                    "routing_key": self.topology.dlq_telemetry_routing_key,
                    "reason": f"malformed_json: {exc}",
                    "raw_payload": str(raw_payload),
                }
            )
            report.dlq_rejected_count += 1
            return report

        if (
            isinstance(decoded, dict)
            and "readings" in decoded
            and isinstance(decoded["readings"], list)
        ):
            items = decoded["readings"]
        elif isinstance(decoded, list):
            items = decoded
        elif isinstance(decoded, dict):
            items = [decoded]
        else:
            self.dead_letter_messages.append(
                {
                    "routing_key": self.topology.dlq_telemetry_routing_key,
                    "reason": "unsupported_payload_type",
                    "raw_payload": str(decoded),
                }
            )
            report.dlq_rejected_count += 1
            return report

        partitioned_candidates: dict[str, list[tuple[int, OrderedTelemetryEvent]]] = defaultdict(
            list
        )

        for idx, item in enumerate(items):
            try:
                normalized = self._validate_and_normalize(item)
            except Exception as exc:
                self.dead_letter_messages.append(
                    {
                        "routing_key": self.topology.dlq_telemetry_routing_key,
                        "reason": str(exc),
                        "raw_payload": item,
                    }
                )
                report.dlq_rejected_count += 1
                continue

            if not self.deduplicator.check_and_record(
                normalized.idempotency_key, recorded_at=normalized.observed_at
            ):
                self.duplicate_keys.append(normalized.idempotency_key)
                report.duplicate_count += 1
                continue

            partitioned_candidates[normalized.sensor_id].append((idx, normalized))

        # Sort strictly by (observed_at, original_index) within each sensor partition (SCRUM-148)
        for sensor_id, indexed_events in partitioned_candidates.items():
            ordered_events = [
                ev
                for _, ev in sorted(indexed_events, key=lambda pair: (pair[1].observed_at, pair[0]))
            ]
            existing = self.processed_by_sensor[sensor_id]
            if (
                existing
                and ordered_events
                and ordered_events[0].observed_at < existing[-1].observed_at
            ):
                combined = sorted(
                    [*existing, *ordered_events],
                    key=lambda ev: ev.observed_at,
                )
                existing.clear()
                existing.extend(combined)
            else:
                existing.extend(ordered_events)

            report.ordered_by_sensor[sensor_id] = ordered_events
            report.accepted_count += len(ordered_events)

            if self.on_ordered_event is not None:
                for ev in ordered_events:
                    self.on_ordered_event(ev)

        return report
