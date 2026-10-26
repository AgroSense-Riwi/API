"""AgroSense Café — CloudWatch Observability, Alarms Configuration and Health Monitoring.

Implements infrastructure monitoring logic for:
- SCRUM-192: Amazon CloudWatch Log Groups, Metric Filters and Container Insights.
- SCRUM-193: SNS Alarm Thresholds for API Latency, ECS CPU/Memory and RabbitMQ DLQ Depth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AlarmState(str, Enum):
    OK = "OK"
    ALARM = "ALARM"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class CloudWatchMetricThreshold:
    """Definición canónica de umbral de alarma en Amazon CloudWatch."""

    metric_name: str
    namespace: str
    threshold: float
    comparison_operator: str  # e.g., GreaterThanOrEqualToThreshold
    evaluation_periods: int
    period_seconds: int
    statistic: str = "Average"
    sns_topic_arn: str = "arn:aws:sns:us-east-1:123456789012:agrosense-ops-alerts"


class CloudWatchObservabilityManager:
    """Gestor de observabilidad, filtros métricos y alarmas proactivas."""

    # 1. Definición de Log Groups con retención de 7 días (SCRUM-192)
    REQUIRED_LOG_GROUPS = [
        {"name": "/aws/ecs/agrosense-api", "retention_days": 7},
        {"name": "/aws/ecs/agrosense-broker", "retention_days": 7},
        {"name": "/aws/ecs/agrosense-airflow", "retention_days": 7},
    ]

    # 2. Umbrales críticos según SCRUM-193
    THRESHOLDS = {
        # Latencia API P95 > 2.0 segundos durante 2 periodos consecutivos
        "api_latency": CloudWatchMetricThreshold(
            metric_name="TargetResponseTime",
            namespace="AWS/ApplicationELB",
            threshold=2.0,
            comparison_operator="GreaterThanOrEqualToThreshold",
            evaluation_periods=2,
            period_seconds=60,
            statistic="p95",
        ),
        # Utilización de CPU ECS > 75%
        "ecs_cpu": CloudWatchMetricThreshold(
            metric_name="CPUUtilization",
            namespace="AWS/ECS",
            threshold=75.0,
            comparison_operator="GreaterThanOrEqualToThreshold",
            evaluation_periods=2,
            period_seconds=60,
            statistic="Average",
        ),
        # Utilización de Memoria ECS > 80%
        "ecs_memory": CloudWatchMetricThreshold(
            metric_name="MemoryUtilization",
            namespace="AWS/ECS",
            threshold=80.0,
            comparison_operator="GreaterThanOrEqualToThreshold",
            evaluation_periods=2,
            period_seconds=60,
            statistic="Average",
        ),
        # Profundidad de Dead Letter Queue de RabbitMQ > 10 mensajes
        "rabbitmq_dlq_depth": CloudWatchMetricThreshold(
            metric_name="DeadLetterQueueDepth",
            namespace="AgroSense/RabbitMQ",
            threshold=10.0,
            comparison_operator="GreaterThanOrEqualToThreshold",
            evaluation_periods=1,
            period_seconds=60,
            statistic="Maximum",
        ),
    }

    @classmethod
    def evaluate_metric_state(
        cls,
        metric_key: str,
        current_value: float,
    ) -> dict[str, Any]:
        """Evalúa un valor métrico frente al umbral configurado."""
        rule = cls.THRESHOLDS.get(metric_key)
        if not rule:
            raise KeyError(f"Métrica '{metric_key}' no configurada.")

        is_alarm = current_value >= rule.threshold
        state = AlarmState.ALARM if is_alarm else AlarmState.OK

        return {
            "metric_key": metric_key,
            "metric_name": rule.metric_name,
            "current_value": current_value,
            "threshold": rule.threshold,
            "state": state.value,
            "sns_topic_arn": rule.sns_topic_arn if is_alarm else None,
            "alert_message": (
                f"ALERTA CRÍTICA: {rule.metric_name} alcanzó {current_value} "
                f"(Umbral máximo: {rule.threshold}). Notificando a SNS."
                if is_alarm
                else "Métrica dentro de parámetros normales."
            ),
        }
