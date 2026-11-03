# AgroSense Café — Operational Runbook & Disaster Recovery Protocol
**Jira Reference:** `SCRUM-189` / Story `AGRO-28` (`SCRUM-47`)
**Compliance Standards:** `E-15`, `RNF-04`, `CT-05`, `CT-09`
**Classification:** Critical Infrastructure & Production Operations

---

## 1. Executive Summary & Objective

This Operational Runbook establishes the standard operating procedures (SOP), automated backup policies, recovery objectives, and step-by-step incident response workflows for the AgroSense Café platform on AWS (`us-east-2`) and Docker Compose.

### Target Recovery Metrics (RNF-04)
- **RTO (Recovery Time Objective):** $< 60\text{ minutes}$ for critical backend services (`agrosense-api-service`, `agrosense-broker`) and PostgreSQL/PostGIS persistence on EC2 (`i-0ee2594ccbe4f06c3`).
- **RPO (Recovery Point Objective):** $< 15\text{ minutes}$ of telemetry data loss in the event of an unrecoverable zone outage (buffered in durable RabbitMQ queues on EFS `fs-06d46462d0c634895` and S3 Bronze `s3://datalakeagrosense/raw/iot_telemetry/`).

---

## 2. Automated Backup Strategy

### 2.1 PostgreSQL 16 + PostGIS (Amazon EC2 `agrosense-db-server` / Docker PostGIS)
- **Automated EBS Snapshots:** Daily EBS volume snapshots (`vol-0d49f022647727989`, tagged `Backup=daily`, `RetentionDays=7`) plus baseline AMI/snapshot `snap-0804529d193adcaf2`.
- **Daily Full Logical Dumps (`pg_dump` to S3):**
  ```bash
  # Trigger manual logical dump of traceability database
  pg_dump -h "${DB_HOST}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
    --format=custom --compress=9 --file="/backups/agrosense_$(date +%Y%m%d_%H%M%S).dump"

  # Sync to secure S3 bucket with Server-Side Encryption (SSE-S3)
  aws s3 cp /backups/ "s3://datalakeagrosense/backups/postgres/" --recursive --sse AES256
  ```

### 2.2 S3 Data Lake (Medallion Architecture — `s3://datalakeagrosense`)
- **Lifecycle Policies (`Tema #7` — Active on `datalakeagrosense`):**
  - `raw/` (`bronze-raw-retention-policy`): Transition to `STANDARD_IA` after 30 days, `GLACIER` after 90 days, expire after 365 days (`AbortIncompleteMultipartUpload` at 7 days).
  - `silver/` (`silver-clean-retention-policy`): Transition to `STANDARD_IA` after 60 days, `GLACIER` after 180 days, expire after 730 days.
  - `gold/` (`gold-analytics-intelligent-tiering`): Transition to `STANDARD_IA` after 90 days; Snappy Parquet partitions registered in AWS Glue / Athena (`agrosense_datalake`).

### 2.3 RabbitMQ State & Topology
- Queue definitions and user policies stored in `infra/docker/rabbitmq/definitions.json` and declared idempotently at runtime via `declare_rabbitmq_topology` on vhost `/agrosense`.
- Dead Letter Queue (`dead.letter.queue`) persisted on Amazon EFS (`fs-06d46462d0c634895`, access point `fsap-0d7691f52db4faa05`).

---

## 3. Incident Severity Levels & Response Matrix

| Severity | Definition | Target Response | Escalation Path |
| :--- | :--- | :--- | :--- |
| **SEV-1 (Critical)** | Core REST API down (`agrosense-api-alb-5xx-alarm` / `agrosense-api-unhealthy-hosts-alarm`), EC2 PostGIS unreachable, or data corruption in Gold export batches. | $< 15\text{ min}$ | DevOps Lead & Security Officer |
| **SEV-2 (Major)** | RabbitMQ ingestion stopped, DLQ spike (`agrosense-dlq-messages-alarm` $\ge 1$ msg), or satellite DAG `dag_satellite_ingestion` failed. | $< 30\text{ min}$ | Data Engineer on-call |
| **SEV-3 (Moderate)** | Spark batch processing SLA delayed, Power BI refresh failure, secondary API latency $> 500\text{ ms}$. | $< 2\text{ hours}$ | Analytics / Backend Engineer |

---

## 4. Disaster Recovery & Restoration Procedures

### 4.1 Restoring PostgreSQL from S3 Dump
1. **Provision/Verify clean database container or EC2 instance:**
   ```bash
   docker compose up -d db
   ```
2. **Download latest verified backup:**
   ```bash
   aws s3 cp "s3://datalakeagrosense/backups/postgres/latest.dump" /tmp/latest.dump
   ```
3. **Execute clean restoration with PostGIS extensions:**
   ```bash
   psql -U "${POSTGRES_USER}" -d postgres -c "DROP DATABASE IF EXISTS ${POSTGRES_DB};"
   psql -U "${POSTGRES_USER}" -d postgres -c "CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};"
   pg_restore -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --no-owner --role="${POSTGRES_USER}" /tmp/latest.dump
   ```
4. **Run Alembic migration check (up to head `d4e8f1a2b3c4`) and apply non-owner RLS role:**
   ```bash
   uv run alembic upgrade head
   psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -f infra/aws/ec2-postgres-app-role.sql
   ```

### 4.2 Restoring RabbitMQ Topology & DLQ Inspection
1. **Verify RabbitMQ health on vhost `/agrosense`:**
   ```bash
   docker compose exec -T broker rabbitmq-diagnostics -q ping
   ```
2. **Inspect and re-process messages from `dead.letter.queue` after payload fix:**
   ```bash
   # Check dead.letter.queue depth in vhost /agrosense
   docker compose exec -T broker rabbitmqctl list_queues -p /agrosense name messages_ready messages_unacknowledged
   # Restart FastConsumer worker to resume ordered consumption and S3 Bronze persistence
   uv run python -m agrosense_api.workers.telemetry_consumer
   ```

### 4.3 Live CloudWatch Alarm Verification (`CT-09`)
```bash
# Trigger controlled DLQ fault injection and verify live CloudWatch -> SNS alarm transition
./infra/aws/setup-alarms-ct09.sh trigger-demo

# Restore operational state to OK after verification
./infra/aws/setup-alarms-ct09.sh reset-demo
```

---

## 5. Security Incident Response (Credential / Secret Leakage)
If an API token, JWT secret, or Buyer API key is compromised:
1. **Immediate Revocation:** Rotate `agrosense/prod/jwt-secret` and `agrosense/prod/buyer-jwt-secret` in AWS Secrets Manager, or revoke a specific buyer key via `./infra/aws/buyer-keys.sh revoke <CLIENT_ID>`.
2. **Session Invalidation:** Force new ECS deployment (`aws ecs update-service --cluster agrosense-cluster --service agrosense-api-service --force-new-deployment`) so all active JWTs fail signature verification immediately.
3. **Audit Trails:** Inspect CloudWatch logs (`/ecs/agrosense-api`) and PostgreSQL table `buyer_api_audit_logs` for unauthorized access attempts (`status_code = 403`).
