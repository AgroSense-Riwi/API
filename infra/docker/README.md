# Topología Docker Compose — AgroSense Café

Infraestructura de contenedores alineada con una arquitectura de red de doble capa (VPC Public/Private Subnets) e integración directa con **Amazon S3**.

## 1. Diagrama de Topología de Red y Volúmenes

```text
                       [ HOST / CLIENTES EXTERNOS ]
                             |              |
              :${API_PORT:-8000}          :${AIRFLOW_PORT:-8080}
                             |              |
+----------------------------v--------------v----------------------------+
|  agrosense_public_subnet (driver: bridge | Ingress Tier)               |
|                                                                        |
|   +------------------------+              +------------------------+   |
|   |     agrosense_api      |              |  agrosense_airflow_    |   |
|   |   (FastAPI / Uvicorn)  |              |       webserver        |   |
|   +-----------+------------+              +-----------+------------+   |
+---------------|---------------------------------------|----------------+
                |                                       |
+---------------v---------------------------------------v----------------+
|  agrosense_private_subnet (driver: bridge | internal: true | Data Tier)|
|                                                                        |
|   +------------------------+              +------------------------+   |
|   |   agrosense_migrate    |              |  agrosense_airflow_    |   |
|   | (alembic upgrade head) |              |   scheduler / init     |   |
|   +-----------+------------+              +-----------+------------+   |
|               |                                       |                |
|               v                                       v                |
|   +------------------------+              +------------------------+   |
|   |      agrosense_db      |              |    agrosense_broker    |   |
|   |  (PostgreSQL+PostGIS)  |              |  (RabbitMQ 3.13 Mgmt)  |   |
|   +-----------+------------+              +-----------+------------+   |
|               |                                       |                |
+---------------|---------------------------------------|----------------+
                v                                       v
       (pg_spatial_data)                   (iot_telemetry_queue_data)
```

## 2. Matriz de Servicios, Redes y Puertos

| Servicio | Contenedor | Redes | Puertos Host | Volumen / Bind Mount |
| :--- | :--- | :--- | :--- | :--- |
| **db** | agrosense_db | private_subnet | Ninguno (Aislado) | `pg_spatial_data`, `infra/docker/postgres/init` |
| **broker** | agrosense_broker | private_subnet | Ninguno (Aislado) | `iot_telemetry_queue_data` |
| **migrate** | agrosense_migrate | private_subnet | Ninguno (One-shot) | N/A |
| **api** | agrosense_api | public_subnet, private_subnet | `${API_PORT:-8000}:8000` | N/A |
| **airflow-init** | agrosense_airflow_init | private_subnet | Ninguno (One-shot) | `airflow_logs_data`, `dags`, `plugins` |
| **airflow-webserver** | agrosense_airflow_webserver | public_subnet, private_subnet | `${AIRFLOW_PORT:-8080}:8080` | `airflow_logs_data`, `dags`, `plugins` |
| **airflow-scheduler** | agrosense_airflow_scheduler | private_subnet | Ninguno (Aislado) | `airflow_logs_data`, `dags`, `plugins` |

## 3. Comandos Operativos

```bash
# Validar sintaxis y variables interpoladas del manifiesto
docker compose config

# Construir imágenes y levantar toda la plataforma en segundo plano
docker compose up -d --build

# Consultar el estado de salud (healthchecks) de los contenedores
docker compose ps

# Inspeccionar logs en tiempo real
docker compose logs -f api airflow-scheduler

# Detener contenedores y eliminar redes y volúmenes persistentes
docker compose down -v

# Modo depuración local (expone Postgres en 127.0.0.1:5433 y RabbitMQ UI en 127.0.0.1:15672)
docker compose -f docker-compose.yml -f docker-compose.override.example.yml up -d
```
