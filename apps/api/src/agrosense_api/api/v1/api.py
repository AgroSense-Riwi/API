from fastapi import APIRouter

from agrosense_api.api.v1.endpoints import auth, health, telemetry, traceability

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router, prefix="/api/v1")
api_router.include_router(telemetry.router, prefix="/api/v1")
api_router.include_router(traceability.router, prefix="/api/v1")
