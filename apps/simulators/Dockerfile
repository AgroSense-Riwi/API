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
COPY apps/simulators/pyproject.toml ./apps/simulators/pyproject.toml

# Instalar dependencias de producción del simulador
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev --package agrosense-simulators

# 2. Copiar el código fuente del simulador e instalar el paquete
COPY apps/simulators ./apps/simulators
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --package agrosense-simulators


# ==============================================================================
# Stage 2: Runtime - Imagen ligera no-root para producción
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

# Copiar entorno virtual y artefactos de apps/simulators
COPY --from=builder --chown=agrosense:agrosense /app/.venv /app/.venv
COPY --from=builder --chown=agrosense:agrosense /app/apps/simulators /app/apps/simulators

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER agrosense

# Entrypoint por defecto ejecutando el simulador en modo dev/batch
CMD ["python", "-m", "agrosense_simulators.main", "--mode", "dev"]
