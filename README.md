# AgroSense Café — Monorepo de Trazabilidad Cafetera y Monitoreo Agroclimático IoT

Plataforma empresarial para el monitoreo agronómico IoT, aseguramiento de calidad sensorial (SCA), orquestación de datos hacia Amazon S3 y verificación geoespacial de cero deforestación (PostGIS) para fincas cafeteras en Colombia.

---

## 1. Estructura del Monorepo

- `apps/api/`: Backend REST API construido con **FastAPI**, **SQLAlchemy 2.0**, **GeoAlchemy2 (PostGIS)** y migraciones automáticas con **Alembic**.
- `apps/simulators/`: Simuladores de telemetría IoT agroclimática hacia **RabbitMQ**.
- `pipelines/airflow/`: DAGs y plugins de **Apache Airflow** (`LocalExecutor`) para ingesta y procesamiento Medallion en **Amazon S3**.
- `infra/docker/`: Scripts de inicialización de base de datos (`postgis`, `pgcrypto`, `airflow_db`) y documentación de topología de red.
- `scripts/`: Herramientas operativas y verificación automatizada de arranque (`verify_bootstrap.sh`).

---

## 2. Requisitos Previos

Para levantar y operar el entorno contenerizado o de desarrollo local asegúrate de contar con:

- **Docker Engine**: `v24.0+`
- **Docker Compose**: `v2.20+` (`docker compose` plugin)
- **Python**: `3.12+`
- **uv** (Gestor de paquetes y workspaces de Python): `0.5+`

---

## 3. Bootstrapping (Arranque con un Solo Comando — CT-01)

El entorno completo (PostgreSQL/PostGIS, RabbitMQ, FastAPI con migraciones automáticas de Alembic y Apache Airflow) está diseñado para arrancar desde cero en **menos de 30 minutos** mediante un único flujo de comandos:

```bash
# 1. Crear el archivo de variables de entorno locales a partir de la plantilla y levantar el stack
cp .env.example .env && docker compose up -d --build
```

> **Nota sobre `.env` (CT-02)**: Antes de usar en entornos compartidos, reemplaza los valores placeholder `changeme` en tu archivo `.env` local (especialmente `POSTGRES_PASSWORD`, `RABBITMQ_DEFAULT_PASS`, `AGROSENSE_JWT_SECRET_KEY` y `AIRFLOW__CORE__FERNET_KEY`).

### Verificación Automatizada de Bootstrapping (SLA < 30 min)
Para ejecutar la prueba automatizada que cronometra el arranque completo y verifica que todos los servicios pasen sus healthchecks dentro del SLA de 30 minutos (1800s):

```bash
./scripts/verify_bootstrap.sh
```

---

## 4. Monitoreo y Verificación de Salud

Una vez iniciado el stack, utiliza los siguientes comandos para inspeccionar el estado operativo de los contenedores y las migraciones automáticas:

```bash
# 1. Consultar el estado de salud (healthy / exited 0) de todos los contenedores
docker compose ps

# 2. Inspeccionar en tiempo real los logs de la API (incluye la ejecución de Alembic en entrypoint.sh)
docker compose logs -f api

# 3. Validar el endpoint HTTP de salud de FastAPI
curl -f http://localhost:8000/health
```

---

## 5. Protocolo de Teardown (Apagado Seguro — E-04)

Dependiendo del escenario operativo, sigue el nivel de apagado correspondiente:

### 5.1. Apagado Estándar (Preservando Datos y Volúmenes)
Detiene y elimina los contenedores y las redes virtuales (`agrosense_public_subnet` y `agrosense_private_subnet`), pero conserva intactos los datos persistentes en los volúmenes nombrados (`pg_spatial_data`, `iot_telemetry_queue_data`, `airflow_logs_data`):

```bash
docker compose down
```

### 5.2. Apagado con Limpieza Total (Reiniciar Estado desde Cero)
Detiene los contenedores, elimina contenedores huérfanos y destruye todos los volúmenes nombrados junto con los datos de PostGIS, colas de RabbitMQ y logs de Airflow (útil para probar migraciones o el script de inicialización desde cero):

```bash
docker compose down -v --remove-orphans
```

### 5.3. Recuperación ante Desastres y Limpieza de Recursos Huérfanos
En caso de cierres abruptos del host, redes bloqueadas o acumulación de imágenes/contenedores huérfanos que impidan un arranque limpio:

```bash
# 1. Forzar el desmontaje completo del proyecto actual
docker compose down -v --remove-orphans

# 2. Podar contenedores detenidos, redes sin uso e imágenes colgantes (dangling)
docker system prune -f

# 3. (Opcional) Eliminar volúmenes anónimos o huérfanos remanentes en el host
docker volume prune -f
```

---

## 6. Desarrollo Local y Calidad de Código

```bash
# Sincronizar dependencias del workspace con uv
uv sync --locked

# Ejecutar linters, formateadores y escaneo de secretos (gitleaks)
uv run pre-commit run --all-files

# Ejecutar la suite de pruebas automatizadas con Pytest
uv run pytest
```
