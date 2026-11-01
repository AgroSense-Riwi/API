"""Healthchecks (healthcheck_strategy.md §6).

* ``GET /health`` — readiness: verifica PostgreSQL (``SELECT 1``) y RabbitMQ (conexión TCP
  al puerto AMQP). Responde 200 ``healthy`` o 503 ``unhealthy`` con el detalle por
  dependencia. Lo usa el healthcheck de Docker Compose y el monitoreo.
* ``GET /health/live`` — liveness: sólo confirma que el proceso atiende HTTP. Lo usan el
  healthcheck del contenedor ECS y el target group del ALB, para que una caída transitoria
  de la BD o del broker no provoque reinicios en cascada de las tareas (flapping).
"""

import asyncio
import logging
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from agrosense_api.core.config import get_settings
from agrosense_api.db.session import engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

CHECK_TIMEOUT_SECONDS = 2.0
DependencyStatus = Literal["connected", "disconnected", "not_configured", "not_checked"]


class HealthResponse(BaseModel):
    status: Literal["healthy", "unhealthy"]
    database: DependencyStatus
    rabbitmq: DependencyStatus
    # La API no usa S3 en el camino de las peticiones; se informa pero no se verifica.
    storage: DependencyStatus = "not_checked"


class LivenessResponse(BaseModel):
    status: Literal["alive"] = "alive"
    service: str = "agrosense-api"


async def check_database() -> DependencyStatus:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception:  # cualquier fallo (red, auth, timeout) significa "no disponible"
        logger.warning("Health check: database unreachable", exc_info=True)
        return "disconnected"
    return "connected"


async def check_rabbitmq() -> DependencyStatus:
    broker_url = get_settings().rabbitmq_url
    if broker_url is None or not broker_url.get_secret_value():
        return "not_configured"
    parsed = urlparse(broker_url.get_secret_value())
    host = parsed.hostname or "localhost"
    port = parsed.port or (5671 if parsed.scheme == "amqps" else 5672)
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            _reader, writer = await asyncio.open_connection(host, port)
        writer.close()
        await writer.wait_closed()
    except (OSError, TimeoutError):
        logger.warning("Health check: RabbitMQ unreachable at %s:%s", host, port)
        return "disconnected"
    return "connected"


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "Alguna dependencia no responde"}},
)
async def health_check(response: Response) -> HealthResponse:
    database, rabbitmq = await asyncio.gather(check_database(), check_rabbitmq())
    healthy = database == "connected" and rabbitmq == "connected"
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="healthy" if healthy else "unhealthy",
        database=database,
        rabbitmq=rabbitmq,
    )


@router.get("/health/live", response_model=LivenessResponse)
async def liveness_check() -> LivenessResponse:
    return LivenessResponse()
