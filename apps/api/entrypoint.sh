#!/usr/bin/env bash
set -euo pipefail

log_info() {
    echo "[AgroSense Entrypoint] [INFO] $*"
}

log_error() {
    echo "[AgroSense Entrypoint] [ERROR] $*" >&2
}

SKIP_MIGRATIONS="${SKIP_MIGRATIONS:-false}"
DB_MAX_RETRIES="${DB_MAX_RETRIES:-30}"
DB_RETRY_INTERVAL="${DB_RETRY_INTERVAL:-2}"

if [[ "${SKIP_MIGRATIONS,,}" == "true" || "${SKIP_MIGRATIONS}" == "1" ]]; then
    log_info "SKIP_MIGRATIONS=${SKIP_MIGRATIONS} detected. Skipping Alembic database migrations."
else
    if [[ -z "${DATABASE_URL:-}" ]]; then
        log_error "DATABASE_URL environment variable is not set. Cannot run Alembic migrations."
        exit 1
    fi

    log_info "Waiting for PostgreSQL database to become available..."
    if ! python - <<'PYCODE'
import os
import sys
import time
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

database_url = os.environ["DATABASE_URL"]
max_retries = int(os.environ.get("DB_MAX_RETRIES", "15"))
retry_interval = float(os.environ.get("DB_RETRY_INTERVAL", "2"))
connect_timeout = int(os.environ.get("DB_CONNECT_TIMEOUT", "3"))

connect_args = {"connect_timeout": connect_timeout}
engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)

for attempt in range(1, max_retries + 1):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print(f"[AgroSense Entrypoint] [INFO] PostgreSQL connection verified (attempt {attempt}/{max_retries}).", flush=True)
        sys.exit(0)
    except (OperationalError, SQLAlchemyError, OSError) as exc:
        print(
            f"[AgroSense Entrypoint] [WARN] Database not ready yet "
            f"(attempt {attempt}/{max_retries}): {exc.__class__.__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        if attempt == max_retries:
            sys.exit(1)
        time.sleep(retry_interval)
PYCODE
    then
        log_error "PostgreSQL did not become available after ${DB_MAX_RETRIES} attempts. Aborting startup."
        exit 1
    fi

    log_info "Running database migrations (alembic upgrade head)..."
    if (cd /app/apps/api && alembic upgrade head); then
        log_info "Database migrations applied successfully."
    else
        exit_code=$?
        log_error "Alembic migration failed with exit code ${exit_code}. Aborting startup to protect schema integrity."
        exit "${exit_code}"
    fi
fi

log_info "Handing over control to PID 1 process: $*"
exec "$@"
