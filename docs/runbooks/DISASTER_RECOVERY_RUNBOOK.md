# AgroSense Café — Operational Runbook & Disaster Recovery Protocol
**Jira Reference:** `SCRUM-189` / Story `AGRO-28` (`SCRUM-47`)  
**Compliance Standards:** `E-15`, `RNF-04`, `CT-05`  
**Classification:** Critical Infrastructure & Production Operations  

---

## 1. Executive Summary & Objective

This Operational Runbook establishes the standard operating procedures (SOP), automated backup policies, recovery objectives, and step-by-step incident response workflows for the AgroSense Café platform.

### Target Recovery Metrics (RNF-04)
- **RTO (Recovery Time Objective):** $< 60\text{ minutes}$ for critical backend services and PostgreSQL persistence.
- **RPO (Recovery Point Objective):** $< 15\text{ minutes}$ of telemetry data loss in the event of an unrecoverable zone outage.

---

## 2. Automated Backup Strategy

### 2.1 PostgreSQL (RDS / Docker PostGIS)
- **Automated Snapshots:** Continuous WAL (Write-Ahead Logging) archiving to Amazon S3 via `pgBackRest` or native RDS automated backups with 7-day retention.
- **Daily Full Logical Dumps:**
  ```bash
  # Trigger manual logical dump of traceability database
  pg_dump -h "${DB_HOST}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
    --format=custom --compress=9 --file="/backups/agrosense_$(date +%Y%m%d_%H%M%S).dump"
  
  # Sync to secure S3 Cold Storage bucket with KMS Server-Side Encryption
  aws s3 cp /backups/ "s3://${BACKUP_S3_BUCKET}/postgres/" --recursive --sse aws:kms --sse-kms-key-id "${KMS_KEY_ARN}"
  ```

### 2.2 S3 Data Lake (Medallion Architecture)
- **Bronze, Silver & Gold Buckets:** S3 Versioning enabled with Multi-Factor Authentication (MFA) Delete protection.
- **Lifecycle Policies:**
  - `Bronze (Raw Telemetry)`: Transition to S3 Glacier Flexible Retrieval after 90 days.
  - `Silver (Normalized Parquet)`: Retain in S3 Standard for 365 days; transition to Glacier Instant Retrieval.
  - `Gold (Star Schema / Fact Tables)`: Retained permanently in S3 Standard for Power BI DirectQuery / Athena ad-hoc queries.

### 2.3 RabbitMQ State & Topology
- Queue definitions and user policies exported automatically to `infra/docker/rabbitmq/definitions.json` upon any topology modification.
- Dead Letter Queue (`dead.letter.queue`) persisted to durable EBS / EFS storage.

---

## 3. Incident Severity Levels & Response Matrix

| Severity | Definition | Target Response | Escalation Path |
| :--- | :--- | :--- | :--- |
| **SEV-1 (Critical)** | Core REST API down, RDS unrecoverable, or data corruption in Gold export batches. | $< 15\text{ min}$ | DevOps Lead & Security Officer |
| **SEV-2 (Major)** | RabbitMQ ingestion stopped, DLQ overflow ($> 1000$ msgs), satellite pipeline failed. | $< 30\text{ min}$ | Data Engineer on-call |
| **SEV-3 (Moderate)** | Spark batch processing SLA delayed, Power BI refresh failure, secondary API latency high. | $< 2\text{ hours}$ | Analytics / Backend Engineer |

---

## 4. Disaster Recovery & Restoration Procedures

### 4.1 Restoring PostgreSQL from S3 Dump
1. **Provision/Verify clean database container or instance:**
   ```bash
   docker compose up -d postgres
   ```
2. **Download latest verified backup:**
   ```bash
   aws s3 cp "s3://${BACKUP_S3_BUCKET}/postgres/latest.dump" /tmp/latest.dump
   ```
3. **Execute clean restoration with PostGIS extensions:**
   ```bash
   psql -U "${POSTGRES_USER}" -d postgres -c "DROP DATABASE IF EXISTS ${POSTGRES_DB};"
   psql -U "${POSTGRES_USER}" -d postgres -c "CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};"
   pg_restore -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --no-owner --role="${POSTGRES_USER}" /tmp/latest.dump
   ```
4. **Run Alembic migration check:**
   ```bash
   uv run alembic upgrade head
   ```

### 4.2 Restoring RabbitMQ Topology & DLQ Draining
1. **Verify RabbitMQ is listening on port 5672 and management on 15672:**
   ```bash
   curl -i -u "${RABBITMQ_USER}:${RABBITMQ_PASS}" http://localhost:15672/api/healthchecks/node
   ```
2. **Re-import topology if cluster crashed without persistence:**
   ```bash
   rabbitmqadmin import /etc/rabbitmq/definitions.json
   ```
3. **Re-route poisoned messages from DLQ after bug resolution:**
   ```bash
   # Run dead-letter consumer worker with exponential backoff retry
   uv run python -m agrosense_api.services.messaging --drain-dlq
   ```

---

## 5. Security Incident Response (Credential / Secret Leakage)
If an API token, JWT secret, or AWS key is compromised:
1. **Immediate Revocation:** Rotate `AGROSENSE_JWT_SECRET_KEY` and `AGROSENSE_BUYER_JWT_SECRET_KEY` in AWS Secrets Manager / `.env`.
2. **Session Invalidation:** All active JWTs will instantly fail signature verification (`InvalidSignatureError`).
3. **Audit Trails:** Inspect CloudWatch logs (`/aws/ecs/agrosense-api`) for unauthorized access patterns.
