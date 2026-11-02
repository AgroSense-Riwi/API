# AgroSense Café API

FastAPI service package. Run from the repository root with:

```powershell
uv run --package agrosense-api fastapi dev apps/api/src/agrosense_api/script.py
```

With Docker Compose (`docker compose up -d --build` from the repository root) the API,
the telemetry consumer, PostgreSQL/PostGIS, RabbitMQ and Airflow start together; see the
root `README.md`.

## Routes

| Method & path | Auth | Purpose |
| --- | --- | --- |
| `GET /health` | public | Readiness: `{status, database, rabbitmq, storage}`; `503` if PostgreSQL or RabbitMQ is unreachable |
| `GET /health/live` | public | Liveness only (used by the ECS container health check and the ALB) |
| `POST /api/v1/auth/token` | OAuth2 form `username`, `password` | Operator token; `username` is the user's email (or the bootstrap user). The token carries the user's real role |
| `POST /api/v1/auth/buyer-token` | JSON `{"client_id", "api_key"}` | Buyer token (`traceability:read`, `buyer_id` claim) after verifying the buyer's Argon2 `api_key_hash` |
| `POST /api/v1/telemetry` | operator token, role `ADMIN` or `AGRONOMIST` | Publishes a reading to `agrosense.ingest.exchange` (routing key `telemetry.block.<farm_id>` → `telemetry.raw.queue`); `202` with `event_id` |
| `GET /api/v1/traceability/batches` | buyer token | Lots contractually assigned to the authenticated buyer |
| `GET /api/v1/traceability/{batch_code}` | buyer token | Traceability/certificate of one of the buyer's lots (`404` if it is not visible to that buyer) |

Interactive documentation: `/docs` (Swagger UI) and `/redoc`; the OpenAPI 3.0.3 contract is
served at `/openapi.json`.

## Configuration

Configure the service with `AGROSENSE_` environment variables:

| Variable | Purpose |
| --- | --- |
| `AGROSENSE_DATABASE_URL` | SQLAlchemy async URL (`postgresql+asyncpg://agrosense_app:...`). Use the non-owner `agrosense_app` role so RLS applies |
| `AGROSENSE_DATABASE_POOL_SIZE` | SQLAlchemy connection pool size (default: 10) |
| `AGROSENSE_DATABASE_MAX_OVERFLOW` | Additional connections allowed above the pool size (default: 20) |
| `AGROSENSE_DATABASE_POOL_TIMEOUT_SECONDS` | Seconds to wait for a pooled connection (default: 30) |
| `AGROSENSE_DATABASE_POOL_RECYCLE_SECONDS` | Connection recycle interval (default: 1800) |
| `AGROSENSE_JWT_SECRET_KEY` | Secret (32+ chars) used to sign operator tokens |
| `AGROSENSE_JWT_ACCESS_TOKEN_MINUTES` | Access-token lifetime (default: 30) |
| `AGROSENSE_BUYER_JWT_SECRET_KEY` | Separate secret (32+ chars) for buyer tokens |
| `AGROSENSE_BUYER_JWT_ISSUER` | Buyer-token issuer (default: `agrosense-buyer-auth`) |
| `AGROSENSE_AUTH_USERNAME` | Optional bootstrap operator (always role `ADMIN`) |
| `AGROSENSE_AUTH_PASSWORD_HASH` | Argon2 hash of the bootstrap operator password |
| `AGROSENSE_AUTH_SCOPES` | JSON array of OAuth scopes granted to issued tokens |
| `AGROSENSE_RABBITMQ_URL` | AMQP URL including the vhost, e.g. `amqp://user:pass@broker:5672/%2Fagrosense` |
| `AGROSENSE_TELEMETRY_CONSUMER_PREFETCH` | Unacknowledged messages held by the consumer (default: 50) |
| `AGROSENSE_ALLOWED_ORIGINS` | JSON array of allowed browser origins |

`DATABASE_URL` (sync, table owner) is used only by Alembic in `entrypoint.sh`.

## Provisioning credentials

Secrets are hashed with Argon2; never commit them.

```powershell
# Hash a password (prompts without echo; also accepts stdin)
uv run --package agrosense-api python -m agrosense_api.cli hash-secret

# New buyer API key: prints the key (hand it to the buyer once) and its hash
uv run --package agrosense-api python -m agrosense_api.cli new-buyer-key
```

Store the results with the owner role (for example from `psql`):

```sql
-- Operator: roles AGRONOMIST, QUALITY_CHIEF, SUSTAINABILITY_COMMITTEE or ADMIN
UPDATE users SET email = 'ana@example.com', password_hash = '<argon2 hash>', is_active = true
WHERE user_id = '<uuid>';

-- Buyer: client_id is the public identifier, api_key_hash the stored secret hash
UPDATE international_buyers SET api_key_hash = '<argon2 hash>', is_active = true
WHERE client_id = '<client_id>';
```

Users or buyers without `password_hash` / `api_key_hash` (e.g. rows created by the seed)
cannot authenticate. Inside Docker Compose run the CLI with
`docker compose run --rm --no-deps --entrypoint python api -m agrosense_api.cli hash-secret`.

## Database, migrations and buyer isolation (RN-11)

Apply migrations with the owner role:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://OWNER:PASSWORD@localhost:5432/agrosense_db"
uv run --package agrosense-api alembic -c apps/api/alembic.ini upgrade head
```

`export_batches` and `buyer_batch_contracts` use `FORCE ROW LEVEL SECURITY` with
deny-by-default policies: the buyer endpoints run `SET LOCAL app.current_buyer_id` (via
`set_config(..., true)`) inside the request transaction, so a buyer only sees rows linked to
its own contracts and a session without that variable sees none. Internal code that needs
every row must authenticate an operator and use `get_operator_db(...)`, which sets
`SET LOCAL app.current_role = 'operator'`. The application role `agrosense_app` must not own
the tables nor have `BYPASSRLS`; Docker creates it in
`infra/docker/postgres/init/02-create-app-role.sh`. On AWS the EC2 server is created by
`infra/aws/cloudformation-db-ec2.yaml` (stack `agrosense-db-ec2`, private IP `10.0.3.10`,
access through SSM Session Manager, key pair stored in Parameter Store), whose first boot
creates both roles, PostGIS and `airflow_db`. For a server created by hand, run
`infra/aws/ec2-postgres-app-role.sql` once.

## Certification rules (RN-08 / RN-09)

`services/certification_engine.py` evaluates a lot:

- RN-08: contributions must be positive and add up to the lot's net weight.
- RN-09: each origin farm is intersected in PostGIS (`ST_Intersects(farms.geom,
  forest_reserves.geom)`); `farms.overlaps_reserve` is kept up to date by the
  `trg_check_farm_forest_overlap` trigger. A farm without geometry or without spatial
  evaluation is **not** compliant, so the lot is rejected.

Load farm perimeters into `farms.geom` and reserve polygons into `forest_reserves.geom`
(both `POLYGON`, SRID 4326) before certifying.

## Telemetry consumer

The FastConsumer runs as its own process (Compose service `telemetry-consumer`, second
container of the API Fargate task):

```powershell
uv run --package agrosense-api python -m agrosense_api.workers.telemetry_consumer
```

It consumes `telemetry.raw.queue` with manual ACK, validates, deduplicates and orders the
readings per sensor, and sends invalid payloads to `dead.letter.queue` through the DLX.

## Tests

```powershell
uv run pytest apps/api/tests
```

Database tests use `TEST_DATABASE_URL` (schema created by the tests) and
`TEST_MIGRATED_DATABASE_URL` (a database migrated with `alembic upgrade head`, used by the
RLS and PostGIS tests); they are skipped when those variables are not set. The SCRUM-171
benchmark reads the Kaggle dataset from `AGROSENSE_CROP_RECOMMENDATION_CSV`.

Rate limits use SlowAPI's in-process storage and should be backed by shared storage when
running multiple workers. The soil recommendations are conservative screening rules, not
fertilizer prescriptions.
