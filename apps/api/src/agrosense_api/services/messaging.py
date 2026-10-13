import json
import logging
from uuid import UUID

import aio_pika

from agrosense_api.core.config import get_settings
from agrosense_api.schemas.telemetry import TelemetryCreate

logger = logging.getLogger(__name__)


class MessageBrokerUnavailable(Exception):
    pass


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
