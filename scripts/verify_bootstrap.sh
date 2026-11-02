#!/usr/bin/env bash
# ==============================================================================
# AgroSense Café — Startup Bootstrapping Verification Script (SCRUM-109 / CT-01)
# Valida que toda la topología multicontenedor arranque y alcance estado healthy
# en menos de 30 minutos (1800 segundos) mediante un solo comando.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

MAX_BOOT_TIME_SECONDS="${MAX_BOOT_TIME_SECONDS:-1800}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-5}"
API_HEALTH_URL="${API_HEALTH_URL:-http://localhost:8000/health}"

CREATED_TEMP_ENV="false"

log_info() {
    echo "[AgroSense Bootstrap] [INFO] $*"
}

log_warn() {
    echo "[AgroSense Bootstrap] [WARN] $*" >&2
}

log_error() {
    echo "[AgroSense Bootstrap] [ERROR] $*" >&2
}

cleanup() {
    if [[ "${CREATED_TEMP_ENV}" == "true" && -f "${REPO_ROOT}/.env" ]]; then
        log_info "Eliminando archivo .env temporal generado para la verificación..."
        rm -f "${REPO_ROOT}/.env"
    fi
}
trap cleanup EXIT

# ------------------------------------------------------------------------------
# 1. Validar o generar archivo .env a partir de .env.example (CT-02)
# ------------------------------------------------------------------------------
if [[ ! -f "${REPO_ROOT}/.env" ]]; then
    if [[ ! -f "${REPO_ROOT}/.env.example" ]]; then
        log_error "No se encontró .env ni .env.example en ${REPO_ROOT}."
        exit 1
    fi
    log_warn "Archivo .env no encontrado. Generando .env temporal desde .env.example..."
    cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
    CREATED_TEMP_ENV="true"

    # Generar una clave Fernet válida de 32 bytes base64 URL-safe si tiene el placeholder 'changeme'
    if grep -q "^AIRFLOW__CORE__FERNET_KEY=changeme" "${REPO_ROOT}/.env"; then
        TEMP_FERNET_KEY="$(dd if=/dev/urandom bs=32 count=1 2>/dev/null | base64 | tr '+/' '-_')"
        sed -i "s|^AIRFLOW__CORE__FERNET_KEY=changeme|AIRFLOW__CORE__FERNET_KEY=${TEMP_FERNET_KEY}|" "${REPO_ROOT}/.env"
    fi
else
    log_info "Archivo .env detectado correctamente."
fi

# ------------------------------------------------------------------------------
# 2. Iniciar cronómetro y levantar el stack con un solo comando (CT-01)
# ------------------------------------------------------------------------------
START_EPOCH="$(date +%s)"
log_info "Ejecutando arranque de plataforma: docker compose up -d --build (SLA máximo: ${MAX_BOOT_TIME_SECONDS}s)..."

docker compose up -d --build

# ------------------------------------------------------------------------------
# 3. Espera activa de salud de contenedores y endpoint HTTP /health
# ------------------------------------------------------------------------------
HEALTHY_CONTAINERS=(
    "agrosense_db"
    "agrosense_broker"
    "agrosense_api"
    "agrosense_airflow_webserver"
    "agrosense_airflow_scheduler"
)

log_info "Esperando a que los servicios esenciales alcancen estado 'healthy'..."

while true; do
    CURRENT_EPOCH="$(date +%s)"
    ELAPSED_SECONDS=$((CURRENT_EPOCH - START_EPOCH))

    if (( ELAPSED_SECONDS > MAX_BOOT_TIME_SECONDS )); then
        log_error "SLA incumplido: El tiempo de arranque (${ELAPSED_SECONDS}s) superó el límite de ${MAX_BOOT_TIME_SECONDS}s (30 minutos)."
        docker compose ps
        exit 1
    fi

    # Verificar el contenedor one-shot airflow-init
    INIT_STATE="$(docker inspect --format '{{.State.Status}}' agrosense_airflow_init 2>/dev/null || echo "missing")"
    if [[ "${INIT_STATE}" == "exited" ]]; then
        INIT_EXIT_CODE="$(docker inspect --format '{{.State.ExitCode}}' agrosense_airflow_init)"
        if [[ "${INIT_EXIT_CODE}" != "0" ]]; then
            log_error "El contenedor one-shot 'agrosense_airflow_init' falló con código de salida ${INIT_EXIT_CODE}."
            docker compose logs airflow-init
            exit 1
        fi
    fi

    ALL_HEALTHY="true"

    for container in "${HEALTHY_CONTAINERS[@]}"; do
        STATUS="$(docker inspect --format '{{.State.Status}}' "${container}" 2>/dev/null || echo "missing")"
        HEALTH="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${container}" 2>/dev/null || echo "missing")"

        if [[ "${STATUS}" == "exited" || "${STATUS}" == "dead" ]]; then
            log_error "El contenedor '${container}' se detuvo inesperadamente (estado: ${STATUS})."
            docker compose ps
            exit 1
        fi

        if [[ "${HEALTH}" == "unhealthy" ]]; then
            log_error "El contenedor '${container}' reportó estado 'unhealthy'."
            docker compose ps
            exit 1
        fi

        if [[ "${HEALTH}" != "healthy" ]]; then
            ALL_HEALTHY="false"
            break
        fi
    done

    if [[ "${ALL_HEALTHY}" == "true" && "${INIT_STATE}" == "exited" ]]; then
        if curl -fsS "${API_HEALTH_URL}" >/dev/null 2>&1; then
            log_info "Endpoint ${API_HEALTH_URL} respondió HTTP 200 OK."
            break
        else
            log_info "Contenedores healthy; esperando respuesta HTTP en ${API_HEALTH_URL}..."
        fi
    fi

    log_info "Tiempo transcurrido: ${ELAPSED_SECONDS}s / ${MAX_BOOT_TIME_SECONDS}s. Reintentando en ${POLL_INTERVAL_SECONDS}s..."
    sleep "${POLL_INTERVAL_SECONDS}"
done

# ------------------------------------------------------------------------------
# 4. Reporte final de tiempos y estado de la topología
# ------------------------------------------------------------------------------
END_EPOCH="$(date +%s)"
TOTAL_BOOT_TIME=$((END_EPOCH - START_EPOCH))

log_info "Estado final de los contenedores:"
docker compose ps

log_info "======================================================================"
log_info "CT-01 VERIFICADO EXITOSAMENTE"
log_info "Tiempo total de bootstrapping: ${TOTAL_BOOT_TIME} segundos (Límite SLA: ${MAX_BOOT_TIME_SECONDS}s)"
log_info "======================================================================"
exit 0
