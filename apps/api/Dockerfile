# syntax=docker/dockerfile:1

# ==============================================================================
# Stage 1: Builder - Resolución de dependencias del workspace con uv
# ==============================================================================
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 1. Copiar manifiestos del workspace para aprovechar la caché de capas
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml ./apps/api/pyproject.toml

# Instalar únicamente las dependencias de producción del paquete agrosense-api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev --package agrosense-api

# 2. Copiar el código fuente y migraciones de la API e instalar el paquete
COPY apps/api ./apps/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package agrosense-api


# ==============================================================================
# Stage 2: Runtime - Imagen ligera no-root con curl para healthchecks
# ==============================================================================
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Crear usuario y grupo sin privilegios
RUN groupadd --gid 10001 agrosense && \
    useradd --uid 10001 --gid agrosense --shell /bin/bash --create-home agrosense

WORKDIR /app

# Copiar entorno virtual y artefactos de apps/api (incluyendo alembic.ini, migrations/ y entrypoint.sh)
COPY --from=builder --chown=agrosense:agrosense /app/.venv /app/.venv
COPY --from=builder --chown=agrosense:agrosense /app/apps/api /app/apps/api

# Garantizar permisos de ejecución en el script de entrada
RUN chmod 0755 /app/apps/api/entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER agrosense

EXPOSE 8000

ENTRYPOINT ["/app/apps/api/entrypoint.sh"]
CMD ["uvicorn", "agrosense_api.script:app", "--host", "0.0.0.0", "--port", "8000"]
