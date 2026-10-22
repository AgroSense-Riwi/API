"""Automated verification suite for Sharon Milena's Sprint 2 User Stories:
- AGRO-10 (SCRUM-29: SCRUM-128..131): AWS VPC, Multi-AZ Subnets, Security Groups, IAM & KMS.
- AGRO-13 (SCRUM-32: SCRUM-138, SCRUM-140): ECS Fargate TaskDefs, ECR, ALB & Auto-Scaling.
- AGRO-16 (SCRUM-35: SCRUM-147..149): RabbitMQ Exchange/DLX Topology, Burst Ordering & Idempotency.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agrosense_api.services.messaging import (
    FastTelemetryConsumerWorker,
    OrderedTelemetryEvent,
    RabbitMQTopologyConfig,
    declare_rabbitmq_topology,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_agro10_vpc_multi_az_sg_iam_and_endpoint_iac() -> None:
    """Verify SCRUM-128, SCRUM-129, SCRUM-130, SCRUM-131 and CT-03 global tagging in IaC."""
    setup_script = (REPO_ROOT / "infra" / "aws" / "setup-ecs-infra.sh").read_text(encoding="utf-8")
    cfn_template = (REPO_ROOT / "infra" / "aws" / "cloudformation-vpc-iam.yaml").read_text(
        encoding="utf-8"
    )

    # SCRUM-128: VPC & Multi-AZ Public/Private Subnets + IGW + NAT Gateway
    for cidr in ("10.0.0.0/16", "10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24", "10.0.4.0/24"):
        assert cidr in setup_script
        assert cidr in cfn_template

    # SCRUM-129 (AC-02): API_SG port 8000 restricted strictly to ALB_SG (no 0.0.0.0/0 ingress)
    assert '--port 8000 --source-group "${ALB_SG}"' in setup_script
    assert "SourceSecurityGroupId: !Ref AlbSecurityGroup" in cfn_template
    assert '--port 5432 --source-group "${API_SG}"' in setup_script
    assert '--port 5672 --source-group "${API_SG}"' in setup_script

    # SCRUM-130: IAM Least-Privilege Roles & KMS Key Rotation
    assert "rol_1_ecsTaskExecutionRole" in cfn_template
    assert "rol_2_agrosense-api-task-role" in cfn_template
    assert "EnableKeyRotation: true" in cfn_template

    # SCRUM-131 & CT-03: S3 Gateway VPC Endpoint & Mandatory Global Tagging
    assert "create-vpc-endpoint" in setup_script
    assert "S3GatewayVpcEndpoint" in cfn_template
    assert "Key=Project,Value=AgroSense-Cafe" in setup_script
    assert "Key=ManagedBy,Value=IaC" in setup_script


def test_agro13_ecs_fargate_alb_and_autoscaling_config() -> None:
    """Verify SCRUM-138 and SCRUM-140 (ECS Fargate, ECR, ALB /health, and Auto-Scaling)."""
    setup_script = (REPO_ROOT / "infra" / "aws" / "setup-ecs-infra.sh").read_text(encoding="utf-8")
    api_td = json.loads(
        (REPO_ROOT / "infra" / "aws" / "ecs" / "task-definition-api.json").read_text(
            encoding="utf-8"
        )
    )
    broker_td = json.loads(
        (REPO_ROOT / "infra" / "aws" / "ecs" / "task-definition-broker.json").read_text(
            encoding="utf-8"
        )
    )

    # SCRUM-138: Task definitions & ALB health check
    assert api_td["requiresCompatibilities"] == ["FARGATE"]
    assert api_td["networkMode"] == "awsvpc"
    assert broker_td["requiresCompatibilities"] == ["FARGATE"]
    assert '--health-check-path "/health"' in setup_script

    # SCRUM-140: ECS Auto-Scaling policies (CPU 70% & Memory 75%, min=1, max=4)
    assert "register-scalable-target" in setup_script
    assert "--min-capacity 1" in setup_script
    assert "--max-capacity 4" in setup_script
    assert "ECSServiceAverageCPUUtilization" in setup_script
    assert '"TargetValue":70.0' in setup_script
    assert "ECSServiceAverageMemoryUtilization" in setup_script
    assert '"TargetValue":75.0' in setup_script


@pytest.mark.anyio
async def test_agro16_rabbitmq_topology_and_dlx_configuration() -> None:
    """Verify SCRUM-147: durable exchanges, queues, and Dead Letter Exchange (DLX) bindings."""
    defs_path = REPO_ROOT / "infra" / "docker" / "rabbitmq" / "definitions.json"
    defs = json.loads(defs_path.read_text(encoding="utf-8"))

    exchange_names = {ex["name"] for ex in defs["exchanges"]}
    assert "agrosense.ingest.exchange" in exchange_names
    assert "agrosense.dlx.exchange" in exchange_names

    queues_by_name = {q["name"]: q for q in defs["queues"]}
    assert "telemetry.raw.queue" in queues_by_name
    assert "dead.letter.queue" in queues_by_name
    assert (
        queues_by_name["telemetry.raw.queue"]["arguments"]["x-dead-letter-exchange"]
        == "agrosense.dlx.exchange"
    )
    assert (
        queues_by_name["telemetry.raw.queue"]["arguments"]["x-dead-letter-routing-key"]
        == "dlq.telemetry"
    )

    mock_channel = AsyncMock()
    mock_queue = AsyncMock()
    mock_channel.declare_queue.return_value = mock_queue

    topology = await declare_rabbitmq_topology(mock_channel, RabbitMQTopologyConfig())
    assert "telemetry_queue" in topology
    assert "dead_letter_queue" in topology
    assert mock_channel.declare_exchange.await_count == 2
    assert mock_channel.declare_queue.await_count == 3


def test_agro16_ca01_three_day_burst_strict_chronological_ordering_per_sensor() -> None:
    """Verify SCRUM-148 & CA-01: 3-day accumulated burst is ordered chronologically without loss."""
    farm_id = str(uuid4())
    base_time = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
    sensors = ["SENS-SM-001", "SENS-TMP-001"]

    burst_readings = []
    for sensor_id in sensors:
        for hour in range(72):  # 3 full days (72 hourly readings per sensor)
            ts = base_time + timedelta(hours=hour)
            burst_readings.append(
                {
                    "event_id": str(uuid4()),
                    "farm_id": farm_id,
                    "sensor_id": sensor_id,
                    "metric": "SOIL_MOISTURE" if "SM" in sensor_id else "TEMPERATURE",
                    "value": round(20.0 + (hour * 0.1), 2),
                    "unit": "%" if "SM" in sensor_id else "C",
                    "observed_at": ts.isoformat().replace("+00:00", "Z"),
                }
            )

    # Shuffle the 144 readings to simulate out-of-order burst transmission
    rng = random.Random(42)  # noqa: S311
    rng.shuffle(burst_readings)

    emitted: list[OrderedTelemetryEvent] = []
    worker = FastTelemetryConsumerWorker(on_ordered_event=emitted.append)
    report = worker.process_payload({"batch_id": "BATCH-BURST-72H", "readings": burst_readings})

    assert report.accepted_count == 144
    assert report.duplicate_count == 0
    assert report.dlq_rejected_count == 0
    assert len(emitted) == 144

    for sensor_id in sensors:
        sensor_events = worker.processed_by_sensor[sensor_id]
        assert len(sensor_events) == 72
        timestamps = [ev.observed_at for ev in sensor_events]
        assert timestamps == sorted(timestamps)
        assert timestamps[0] == base_time
        assert timestamps[-1] == base_time + timedelta(hours=71)


def test_agro16_idempotent_deduplication_and_dlq_routing() -> None:
    """Verify SCRUM-149: duplicate messages are skipped and corrupted payloads route to DLQ."""
    worker = FastTelemetryConsumerWorker()
    farm_id = str(uuid4())
    valid_event = {
        "event_id": "evt-unique-001",
        "farm_id": farm_id,
        "sensor_id": "SENS-SM-010",
        "metric": "SOIL_MOISTURE",
        "value": 34.5,
        "unit": "%",
        "observed_at": "2026-10-20T10:00:00Z",
    }

    # First delivery -> accepted
    r1 = worker.process_payload(valid_event)
    assert r1.accepted_count == 1
    assert r1.duplicate_count == 0

    # Second delivery (retry/duplicate) -> deduplicated
    r2 = worker.process_payload(valid_event)
    assert r2.accepted_count == 0
    assert r2.duplicate_count == 1
    assert len(worker.processed_by_sensor["SENS-SM-010"]) == 1

    # Malformed JSON & invalid item -> routed to DLQ
    r3 = worker.process_payload("{corrupted-json-payload")
    assert r3.dlq_rejected_count == 1

    r4 = worker.process_payload([{"sensor_id": "SENS-SM-010", "value": "not-a-float"}])
    assert r4.dlq_rejected_count == 1
    assert len(worker.dead_letter_messages) == 2
    assert worker.dead_letter_messages[0]["routing_key"] == "dlq.telemetry"
