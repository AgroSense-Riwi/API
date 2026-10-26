"""Tests automatizados para SCRUM-48 (AGRO-29):
Observabilidad en CloudWatch, alarmas SNS y script de demostración end-to-end.

Cubre:
- SCRUM-192: Log Groups, filtros métricos y Container Insights.
- SCRUM-193: Umbrales de alarma SNS (latencia API, CPU/Memoria ECS, DLQ depth).
- SCRUM-194: Script de demostración integral y sign-off.
"""

from pathlib import Path

import pytest
from agrosense_api.services.observability_service import (
    AlarmState,
    CloudWatchObservabilityManager,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_cloudwatch_log_groups_retention_policy() -> None:
    """SCRUM-192: Log groups configurados con retención de 7 días."""
    groups = CloudWatchObservabilityManager.REQUIRED_LOG_GROUPS
    assert len(groups) >= 3

    group_names = [g["name"] for g in groups]
    assert "/aws/ecs/agrosense-api" in group_names
    assert "/aws/ecs/agrosense-broker" in group_names
    assert "/aws/ecs/agrosense-airflow" in group_names

    for g in groups:
        assert g["retention_days"] == 7


def test_alarm_thresholds_configuration() -> None:
    """SCRUM-193: Verifica umbrales de latencia API, CPU/Memoria y RabbitMQ DLQ."""
    thresholds = CloudWatchObservabilityManager.THRESHOLDS

    # Latencia API P95 > 2.0 s
    assert thresholds["api_latency"].threshold == 2.0
    # CPU ECS > 75%
    assert thresholds["ecs_cpu"].threshold == 75.0
    # Memoria ECS > 80%
    assert thresholds["ecs_memory"].threshold == 80.0
    # RabbitMQ DLQ > 10 mensajes
    assert thresholds["rabbitmq_dlq_depth"].threshold == 10.0


def test_alarm_evaluation_ok_and_alarm_states() -> None:
    """SCRUM-193: Disparo de alarma cuando la métrica supera el umbral configurado."""
    # Caso 1: Latencia normal (0.45 s) -> OK
    ok_res = CloudWatchObservabilityManager.evaluate_metric_state("api_latency", 0.45)
    assert ok_res["state"] == AlarmState.OK.value
    assert ok_res["sns_topic_arn"] is None

    # Caso 2: Latencia degradada (2.8 s) -> ALARM y notificación a SNS
    alarm_res = CloudWatchObservabilityManager.evaluate_metric_state("api_latency", 2.8)
    assert alarm_res["state"] == AlarmState.ALARM.value
    assert "arn:aws:sns" in str(alarm_res["sns_topic_arn"])
    assert "ALERTA CRÍTICA" in alarm_res["alert_message"]


def test_live_demo_script_exists_and_covers_all_phases() -> None:
    """SCRUM-194: Valida que el script de demostración cubra las fases del sistema."""
    demo_script_path = REPO_ROOT / "docs" / "demos" / "LIVE_DEMONSTRATION_SCRIPT.md"
    assert demo_script_path.is_file(), f"Falta script de demo en {demo_script_path}"

    content = demo_script_path.read_text(encoding="utf-8")
    assert "Phase 1: Authentication & Multi-Tenant Buyer Isolation" in content
    assert "Phase 2: Ingestion & Dead Letter Queue Resilience" in content
    assert "Phase 3: Agronomic Processing & Risk Alerts" in content
    assert "Phase 4: Batch Certification & Cryptographic Digital Proof" in content
    assert "Formal System Sign-Off Checklist" in content
