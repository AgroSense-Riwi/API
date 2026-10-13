from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status

from agrosense_api.api.v1.dependencies import get_current_user
from agrosense_api.core.rate_limit import limiter
from agrosense_api.schemas.telemetry import TelemetryAccepted, TelemetryCreate
from agrosense_api.services.messaging import MessageBrokerUnavailable, publish_telemetry

router = APIRouter(
    prefix="/telemetry",
    tags=["telemetry"],
    dependencies=[Depends(get_current_user)],
)


@router.post("", response_model=TelemetryAccepted, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("60/minute")
async def ingest_telemetry(
    request: Request,
    telemetry: TelemetryCreate,
) -> TelemetryAccepted:
    event_id = uuid4()
    try:
        await publish_telemetry(event_id, telemetry)
    except MessageBrokerUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return TelemetryAccepted(event_id=event_id)
