import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from agrosense_api.api.v1.api import api_router
from agrosense_api.core.config import get_settings
from agrosense_api.core.rate_limit import limiter
from agrosense_api.db.session import engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    await engine.dispose()


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "Compradores",
            "description": "Consulta comercial de certificados de los lotes propios.",
        }
    ],
)
app.openapi_version = "3.0.3"
app.state.limiter = limiter
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(api_router)


def _convert_schema_to_openapi_30(value: Any) -> Any:
    if isinstance(value, list):
        return [_convert_schema_to_openapi_30(item) for item in value]
    if not isinstance(value, dict):
        return value

    converted = {key: _convert_schema_to_openapi_30(item) for key, item in value.items()}
    converted.pop("$schema", None)
    any_of = converted.get("anyOf")
    if isinstance(any_of, list):
        non_null = [
            schema
            for schema in any_of
            if not (isinstance(schema, dict) and schema.get("type") == "null")
        ]
        if len(non_null) == 1 and len(non_null) != len(any_of):
            nullable_schema = non_null[0]
            converted.pop("anyOf")
            converted = {**converted, **nullable_schema, "nullable": True}

    for keyword in ("exclusiveMinimum", "exclusiveMaximum"):
        bound = converted.get(keyword)
        if isinstance(bound, (int, float)):
            converted[keyword.removeprefix("exclusive")] = bound
            converted[keyword] = True
    if "const" in converted:
        converted["enum"] = [converted.pop("const")]
    return converted


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema is not None:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version="3.0.3",
        routes=app.routes,
        summary=app.summary,
        description=app.description,
        tags=app.openapi_tags,
        servers=app.servers,
        terms_of_service=app.terms_of_service,
        contact=app.contact,
        license_info=app.license_info,
    )
    error_schema = {"$ref": "#/components/schemas/ErrorResponse"}
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            if isinstance(operation, dict) and "responses" in operation:
                operation["responses"].setdefault(
                    "500",
                    {
                        "description": "Unexpected server error",
                        "content": {"application/json": {"schema": error_schema}},
                    },
                )
    app.openapi_schema = _convert_schema_to_openapi_30(schema)
    return app.openapi_schema


app.openapi = custom_openapi


def _error_response(
    status_code: int, code: str, message: str, details: list[dict[str, Any]] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return _error_response(exc.status_code, "http_error", message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return _error_response(422, "request_validation_error", "Request validation failed", details)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_error_handler(_request: Request, _exc: RateLimitExceeded) -> JSONResponse:
    return _error_response(429, "rate_limit_exceeded", "Request rate limit exceeded")


@app.exception_handler(Exception)
async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled request error for %s %s", request.method, request.url.path)
    return _error_response(500, "internal_server_error", "An internal server error occurred")
