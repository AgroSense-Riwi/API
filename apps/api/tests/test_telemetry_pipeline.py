"""Telemetría IoT: la API publica en la topología documentada y el FastConsumer la consume.

rabbitmq.md: exchange ``agrosense.ingest.exchange`` (topic) -> ``telemetry.raw.queue`` vía
``telemetry.block.#``; ACK manual; DLX ``agrosense.dlx.exchange`` -> ``dead.letter.queue``.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import yaml
from agrosense_api.core.config import get_settings
from agrosense_api.schemas.telemetry import TelemetryCreate
from agrosense_api.services import messaging
from agrosense_api.services.messaging import FastTelemetryConsumerWorker
from agrosense_api.workers.telemetry_consumer import handle_message

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def broker_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGROSENSE_RABBITMQ_URL", "amqp://u:p@broker:5672/%2Fagrosense")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _telemetry() -> TelemetryCreate:
    return TelemetryCreate(
        farm_id=uuid4(), sensor_id="s-1", metric="soil_moisture", value=40.0, unit="%"
    )


@pytest.mark.anyio
async def test_publish_uses_ingest_exchange_and_bound_routing_key(broker_env) -> None:
    telemetry = _telemetry()
    exchange = MagicMock()
    exchange.publish = AsyncMock()
    connection = MagicMock()
    connection.__aenter__ = AsyncMock(return_value=connection)
    connection.__aexit__ = AsyncMock(return_value=False)
    connection.channel = AsyncMock(return_value=MagicMock())

    with (
        patch.object(messaging.aio_pika, "connect_robust", AsyncMock(return_value=connection)),
        patch.object(
            messaging,
            "declare_rabbitmq_topology",
            AsyncMock(return_value={"ingest_exchange": exchange}),
        ) as declare,
    ):
        event_id = uuid4()
        await messaging.publish_telemetry(event_id, telemetry)

    declare.assert_awaited_once()
    exchange.publish.assert_awaited_once()
    message = exchange.publish.await_args.args[0]
    kwargs = exchange.publish.await_args.kwargs
    assert kwargs["routing_key"] == f"telemetry.block.{telemetry.farm_id}"
    assert kwargs["routing_key"].startswith("telemetry.block.")
    assert kwargs["mandatory"] is True
    assert json.loads(message.body)["event_id"] == str(event_id)


def _incoming(body: bytes) -> MagicMock:
    message = MagicMock()
    message.body = body
    message.message_id = "m-1"
    message.ack = AsyncMock()
    message.reject = AsyncMock()
    return message


def _reading(**overrides) -> dict:
    payload = {
        "event_id": str(uuid4()),
        "farm_id": str(uuid4()),
        "sensor_id": "s-1",
        "metric": "soil_moisture",
        "value": 41.0,
        "unit": "%",
        "observed_at": "2026-10-30T10:00:00+00:00",
    }
    payload.update(overrides)
    return payload


@pytest.mark.anyio
async def test_consumer_acks_valid_message_after_processing() -> None:
    worker = FastTelemetryConsumerWorker()
    dlx = MagicMock()
    dlx.publish = AsyncMock()
    message = _incoming(json.dumps(_reading()).encode())

    assert await handle_message(worker, message, dlx) == "acked"
    message.ack.assert_awaited_once()
    message.reject.assert_not_awaited()
    dlx.publish.assert_not_awaited()
    assert sum(len(v) for v in worker.processed_by_sensor.values()) == 1


@pytest.mark.anyio
async def test_consumer_rejects_corrupt_message_to_dlx() -> None:
    worker = FastTelemetryConsumerWorker()
    dlx = MagicMock()
    dlx.publish = AsyncMock()
    message = _incoming(b"{not-json")

    assert await handle_message(worker, message, dlx) == "rejected"
    message.reject.assert_awaited_once_with(requeue=False)
    message.ack.assert_not_awaited()


@pytest.mark.anyio
async def test_consumer_routes_invalid_items_of_partial_burst_to_dlx_and_acks() -> None:
    worker = FastTelemetryConsumerWorker()
    dlx = MagicMock()
    dlx.publish = AsyncMock()
    burst = {"readings": [_reading(), _reading(sensor_id="")]}
    message = _incoming(json.dumps(burst).encode())

    assert await handle_message(worker, message, dlx) == "acked"
    dlx.publish.assert_awaited_once()
    assert dlx.publish.await_args.kwargs["routing_key"] == "dlq.telemetry"
    message.ack.assert_awaited_once()


def test_definitions_use_documented_vhost() -> None:
    defs = json.loads(
        (REPO_ROOT / "infra" / "docker" / "rabbitmq" / "definitions.json").read_text("utf-8")
    )
    for section in ("exchanges", "queues", "bindings"):
        assert {item["vhost"] for item in defs[section]} == {"/agrosense"}


def test_compose_mounts_broker_config_and_runs_consumer() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text("utf-8"))
    services = compose["services"]
    broker_volumes = " ".join(services["broker"]["volumes"])
    assert "rabbitmq.conf:/etc/rabbitmq/rabbitmq.conf:ro" in broker_volumes
    assert "definitions.json:/etc/rabbitmq/definitions.json:ro" in broker_volumes
    consumer = services["telemetry-consumer"]
    assert "agrosense_api.workers.telemetry_consumer" in " ".join(consumer["command"])
    assert consumer["environment"]["SKIP_MIGRATIONS"] == "true"


def test_api_task_definition_colocates_consumer() -> None:
    task_def = json.loads(
        (REPO_ROOT / "infra" / "aws" / "ecs" / "task-definition-api.json").read_text("utf-8")
    )
    names = [c["name"] for c in task_def["containerDefinitions"]]
    assert names == ["agrosense-api", "agrosense-telemetry-consumer"]
    consumer = task_def["containerDefinitions"][1]
    assert "agrosense_api.workers.telemetry_consumer" in " ".join(consumer["command"])
    assert {s["name"] for s in consumer["secrets"]} == {"AGROSENSE_RABBITMQ_URL"}
