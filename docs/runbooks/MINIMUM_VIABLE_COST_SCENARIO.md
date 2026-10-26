# AgroSense Café — Minimum Viable Cost Operating Scenario ($5–$48/month)
**Jira Reference:** `SCRUM-190` / Story `AGRO-28` (`SCRUM-47`)  
**Compliance Standards:** `E-15`, `RNF-04`, `CT-05`  
**Target Environment:** Staging, Low-Volume Production & Pilot Cooperatives  

---

## 1. Cost Optimization Architectural Philosophy

To support smallholder coffee cooperatives and agricultural pilot deployments, the AgroSense platform architecture incorporates a **Zero-Idle-Cost** and **Fargate Spot / Serverless Tiering** strategy. 

This guide defines the resource allocation, autoscaling limits, and configuration switches required to operate the complete platform between **$5.00 USD/month** (minimal testing footprint) and **$48.00 USD/month** (full end-to-end multi-tenant pilot).

---

## 2. Cost Breakdown: Minimum Viable Tier ($5.00 – $18.00 / month)

Ideal for developer staging, continuous integration validation, or single-cooperative off-season operations.

| Service Component | AWS Sizing & Configuration | Pricing Model | Monthly Cost (USD) |
| :--- | :--- | :--- | :--- |
| **Compute (API & Workers)** | ECS Fargate Spot (0.25 vCPU, 0.5 GB RAM) | 1 task on Spot (~$0.0032/hr) | **~$2.30** |
| **Database (PostgreSQL + PostGIS)** | Aurora Serverless v2 ($0.5 ACU min) or EC2 t4g.nano container | Spot / Serverless auto-pause | **~$5.00 – $8.00** |
| **Object Storage (S3 Medallion)** | S3 Standard (10 GB telemetry + Parquet) | Standard tier + Lifecycle | **~$0.25** |
| **Broker (RabbitMQ)** | Single container co-hosted on ECS Spot | Shared compute task | **$0.00 (included)** |
| **Query Engine (Athena)** | Amazon Athena (Pay-per-scan Parquet queries) | ~20 GB scanned/month | **~$0.10** |
| **Networking & NAT** | S3 Gateway VPC Endpoint (Free) + Public Subnet direct IP | Zero NAT Gateway cost | **$0.00** |
| **Total Estimated Cost** | — | — | **~$7.65 – $10.65 / mo** |

> [!NOTE]
> **NAT Gateway Savings Strategy:** Standard AWS NAT Gateways incur a baseline cost of ~$32.00/month per AZ. In the Minimum Viable Scenario, instances reside in public subnets with strict Security Groups (`API_SG` / `ALB_SG`), utilizing the free **S3 Gateway Endpoint** to transfer all telemetry and Parquet data with **$0 data transfer charges**.

---

## 3. Cost Breakdown: Standard Operational Pilot ($30.00 – $48.00 / month)

Designed for active harvest seasons with live sensor streams, daily Spark jobs, and executive Power BI dashboards.

| Service Component | Sizing & Configuration | Pricing Model | Monthly Cost (USD) |
| :--- | :--- | :--- | :--- |
| **ALB (Application Load Balancer)** | 1 ALB across 2 Availability Zones | Standard LCU usage | **~$18.00** |
| **FastAPI Backend (ECS Fargate)** | 1x `0.5 vCPU, 1 GB RAM` (Fargate Spot) with auto-scale to 2 tasks | Autoscaling (CPU 70%, Memory 75%) | **~$7.50** |
| **PostgreSQL PostGIS (t4g.small)** | RDS PostgreSQL Multi-AZ burstable (or 1-yr Savings Plan) | On-Demand / Reserved | **~$15.00** |
| **RabbitMQ Broker** | 1x `0.25 vCPU, 0.5 GB RAM` durable container | Fargate Spot | **~$2.50** |
| **EMR / PySpark Processing** | EMR Serverless (jobs run on-demand for < 15 min daily) | Per-second execution | **~$3.00** |
| **CloudWatch Logs & Metrics** | 5 GB ingestion, 7-day retention limit | Free tier + basic tier | **~$1.50** |
| **Total Pilot Scenario Cost** | — | — | **~$47.50 / month** |

---

## 4. Key Levers for Strict Cost Control

1. **Auto-Stop for Development Environments:**
   - Automatically scale ECS services to `desiredCount: 0` outside business hours (19:00 to 07:00 UTC) via AWS EventBridge and Lambda.
2. **Parquet Compression & Snappy Encoding:**
   - Storing telemetry in Parquet with Snappy compression reduces storage volume by **82%** compared to raw JSON, reducing S3 and Athena query scanning fees proportionally.
3. **Log Retention Capping:**
   - Default CloudWatch log retention is set to `7 days` instead of indefinite, preventing unbounded storage growth.
