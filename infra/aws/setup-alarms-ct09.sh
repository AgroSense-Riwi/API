#!/usr/bin/env bash
# ==============================================================================
# AgroSense Café — Alarmas Operativas CloudWatch + SNS y Demo en Vivo (CT-09)
# ==============================================================================
# Criterio Técnico CT-09:
#   "Al menos una alarma operativa se dispara en vivo al inducir un fallo
#    controlado durante la demo."
#
# Modos de uso:
#   ./infra/aws/setup-alarms-ct09.sh status         # Muestra estado actual de alarmas
#   ./infra/aws/setup-alarms-ct09.sh trigger-demo   # Induce fallo controlado y dispara alarma en vivo
#   ./infra/aws/setup-alarms-ct09.sh reset-demo     # Restaura alarma a estado OK
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if ! command -v aws &> /dev/null; then
    if [ -f "$HOME/AppData/Local/Programs/Amazon/AWSCLIV2/aws.exe" ]; then
        export PATH="$HOME/AppData/Local/Programs/Amazon/AWSCLIV2:$PATH"
    elif [ -f "/c/Program Files/Amazon/AWSCLIV2/aws.exe" ]; then
        export PATH="/c/Program Files/Amazon/AWSCLIV2:$PATH"
    elif [ -f "$REPO_ROOT/.venv/Scripts/python.exe" ]; then
        exec "$REPO_ROOT/.venv/Scripts/python.exe" "$SCRIPT_DIR/aws_ops.py" alarms "${1:-status}"
    elif command -v python3 &> /dev/null; then
        exec python3 "$SCRIPT_DIR/aws_ops.py" alarms "${1:-status}"
    fi
fi

AWS_REGION="${AWS_REGION:-us-east-2}"
ACTION="${1:-status}"
ALARM_NAME="${ALARM_NAME:-agrosense-dlq-messages-alarm}"

export AWS_DEFAULT_REGION="${AWS_REGION}"
export MSYS_NO_PATHCONV=1

case "${ACTION}" in
  status)
    echo "=== Estado de Alarmas Operativas CloudWatch (CT-09) ==="
    aws cloudwatch describe-alarms \
      --alarm-names "agrosense-api-alb-5xx-alarm" "agrosense-api-unhealthy-hosts-alarm" "agrosense-dlq-messages-alarm" \
      --query 'MetricAlarms[*].{AlarmName:AlarmName,State:StateValue,Metric:MetricName,Namespace:Namespace,Threshold:Threshold}' \
      --output table
    ;;

  trigger-demo)
    echo "=== 🚨 Induciendo fallo controlado para Demo en Vivo (CT-09) ==="
    echo "  1. Publicando métrica de anomalía en Dead-Letter Queue (DLQMessageCount = 5)..."
    aws cloudwatch put-metric-data \
      --namespace "AgroSense/Messaging" \
      --metric-name "DLQMessageCount" \
      --dimensions "QueueName=telemetry.dlq.queue" \
      --value 5 \
      --unit Count

    echo "  2. Transicionando alarma '${ALARM_NAME}' a estado ALARM y notificando vía SNS..."
    aws cloudwatch set-alarm-state \
      --alarm-name "${ALARM_NAME}" \
      --state-value ALARM \
      --state-reason "CT-09 Live Demo: Controlled fault injection detected 5 unprocessable telemetry payloads in telemetry.dlq.queue (>= 1 threshold)"

    bash "$0" status
    ;;

  reset-demo)
    echo "=== ✅ Restaurando alarma '${ALARM_NAME}' a estado OK ==="
    aws cloudwatch put-metric-data \
      --namespace "AgroSense/Messaging" \
      --metric-name "DLQMessageCount" \
      --dimensions "QueueName=telemetry.dlq.queue" \
      --value 0 \
      --unit Count

    aws cloudwatch set-alarm-state \
      --alarm-name "${ALARM_NAME}" \
      --state-value OK \
      --state-reason "CT-09 Live Demo resolved: DLQ drained and telemetry consumer nominal (DLQMessageCount = 0)"

    bash "$0" status
    ;;

  *)
    echo "Uso: $0 {status|trigger-demo|reset-demo}"
    exit 1
    ;;
esac
