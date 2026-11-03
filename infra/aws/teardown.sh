#!/usr/bin/env bash
# ==============================================================================
# AgroSense Café — Teardown & Auditoría FinOps (CT-03 / Regla -10% Rúbrica)
# ==============================================================================
# Modos de operación:
#   1. verify  (por defecto): Audita todos los recursos facturables en AWS.
#   2. pause   : Pausa recursos con cobro por hora (ECS=0, EC2=stopped, NAT+EIP=deleted)
#                conservando datos (S3, EFS, EBS, ECR, VPC) para la siguiente demo.
#   3. destroy : Teardown completo post-sustentación (elimina ECS, ALB, NAT, EIP,
#                EFS, CloudFormation, EC2, Alarmas, ECR, VPC y opcionalmente S3).
#
# Uso:
#   ./infra/aws/teardown.sh verify
#   ./infra/aws/teardown.sh pause
#   CONFIRM_DESTROY=yes ./infra/aws/teardown.sh destroy
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
        exec "$REPO_ROOT/.venv/Scripts/python.exe" "$SCRIPT_DIR/aws_ops.py" teardown "${1:-verify}"
    elif command -v python3 &> /dev/null; then
        exec python3 "$SCRIPT_DIR/aws_ops.py" teardown "${1:-verify}"
    fi
fi

AWS_REGION="${AWS_REGION:-us-east-2}"
PROJECT_NAME="${PROJECT_NAME:-agrosense}"
CLUSTER="${CLUSTER:-agrosense-cluster}"
MODE="${1:-verify}"

export AWS_DEFAULT_REGION="${AWS_REGION}"
export MSYS_NO_PATHCONV=1
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

verify_resources() {
  echo "============================================================"
  echo " AgroSense Café — Auditoría de Recursos Facturables (${AWS_REGION})"
  echo "============================================================"

  echo "[1/7] Instancias EC2:"
  aws ec2 describe-instances \
    --query 'Reservations[*].Instances[*].{ID:InstanceId,Type:InstanceType,State:State.Name,Name:Tags[?Key==`Name`]|[0].Value}' \
    --output table

  echo "[2/7] Servicios ECS (${CLUSTER}):"
  for svc in agrosense-api-service agrosense-broker agrosense-airflow; do
    aws ecs describe-services --cluster "${CLUSTER}" --services "${svc}" \
      --query 'services[*].{Service:serviceName,Status:status,Desired:desiredCount,Running:runningCount}' \
      --output table 2>/dev/null || true
  done

  echo "[3/7] NAT Gateways activos:"
  aws ec2 describe-nat-gateways --filter "Name=state,Values=available,pending" \
    --query 'NatGateways[*].{ID:NatGatewayId,State:State,Subnet:SubnetId}' \
    --output table

  echo "[4/7] Elastic IPs asignadas:"
  aws ec2 describe-addresses \
    --query 'Addresses[*].{AllocationId:AllocationId,PublicIp:PublicIp,Service:ServiceManaged,InstanceId:InstanceId}' \
    --output table

  echo "[5/7] Load Balancers (ALB):"
  aws elbv2 describe-load-balancers \
    --query 'LoadBalancers[*].{Name:LoadBalancerName,State:State.Code,DNS:DNSName}' \
    --output table

  echo "[6/7] Sistemas de archivos EFS:"
  aws efs describe-file-systems \
    --query 'FileSystems[*].{ID:FileSystemId,State:LifeCycleState,Bytes:SizeInBytes.Value}' \
    --output table

  echo "[7/7] Verificación de Tags Obligatorios CT-03 (proyecto, ambiente, responsable):"
  UNTAGGED=$(aws resourcegroupstaggingapi get-resources \
    --query 'ResourceTagMappingList[?!(Tags[?Key==`proyecto`]) || !(Tags[?Key==`ambiente`]) || !(Tags[?Key==`responsable`])].ResourceARN' \
    --output text)
  if [[ -z "${UNTAGGED}" ]]; then
    echo "  ✅ 100% de recursos etiquetados con (proyecto, ambiente, responsable)."
  else
    echo "  ⚠️ Recursos pendientes de tag CT-03: ${UNTAGGED}"
  fi
}

pause_resources() {
  echo "============================================================"
  echo " AgroSense Café — Modo PAUSE (Ahorro de costos entre sesiones)"
  echo "============================================================"

  echo "[1/3] Escalando servicios ECS a desiredCount=0..."
  for svc in agrosense-api-service agrosense-broker agrosense-airflow; do
    aws ecs update-service --cluster "${CLUSTER}" --service "${svc}" --desired-count 0 >/dev/null 2>&1 || true
    echo "  ↳ ${svc} -> desiredCount=0"
  done

  echo "[2/3] Deteniendo instancias EC2 en ejecución..."
  RUNNING_EC2=$(aws ec2 describe-instances \
    --filters "Name=instance-state-name,Values=running" \
    --query 'Reservations[*].Instances[*].InstanceId' --output text)
  if [[ -n "${RUNNING_EC2}" && "${RUNNING_EC2}" != "None" ]]; then
    aws ec2 stop-instances --instance-ids ${RUNNING_EC2} >/dev/null
    echo "  ↳ Instancias detenidas: ${RUNNING_EC2}"
  else
    echo "  ↳ Ninguna instancia EC2 en estado running."
  fi

  echo "[3/3] Eliminando NAT Gateway y liberando Elastic IP..."
  bash "${SCRIPT_DIR}/nat-toggle.sh" down

  verify_resources
}

destroy_resources() {
  if [[ "${CONFIRM_DESTROY:-no}" != "yes" ]]; then
    echo "❌ Para ejecutar el teardown destructivo post-sustentación, ejecuta:"
    echo "   CONFIRM_DESTROY=yes ./infra/aws/teardown.sh destroy"
    exit 1
  fi

  echo "============================================================"
  echo " ⚠️ AgroSense Café — TEARDOWN COMPLETO POST-SUSTENTACIÓN (CT-03)"
  echo "============================================================"

  pause_resources

  echo "[4/10] Eliminando servicios y cluster ECS..."
  for svc in agrosense-api-service agrosense-broker agrosense-airflow; do
    aws ecs delete-service --cluster "${CLUSTER}" --service "${svc}" --force >/dev/null 2>&1 || true
  done
  aws ecs delete-cluster --cluster "${CLUSTER}" >/dev/null 2>&1 || true

  echo "[5/10] Eliminando Application Load Balancer y Target Groups..."
  ALB_ARN=$(aws elbv2 describe-load-balancers --names "${PROJECT_NAME}-alb" \
    --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || echo "")
  if [[ -n "${ALB_ARN}" && "${ALB_ARN}" != "None" ]]; then
    aws elbv2 delete-load-balancer --load-balancer-arn "${ALB_ARN}" 2>/dev/null || true
    sleep 10
  fi
  TG_ARN=$(aws elbv2 describe-target-groups --names "${PROJECT_NAME}-api-tg" \
    --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || echo "")
  if [[ -n "${TG_ARN}" && "${TG_ARN}" != "None" ]]; then
    aws elbv2 delete-target-group --target-group-arn "${TG_ARN}" 2>/dev/null || true
  fi

  echo "[6/10] Eliminando Mount Targets y sistema de archivos EFS..."
  for fs_id in $(aws efs describe-file-systems --query 'FileSystems[*].FileSystemId' --output text); do
    for mt_id in $(aws efs describe-mount-targets --file-system-id "${fs_id}" --query 'MountTargets[*].MountTargetId' --output text); do
      aws efs delete-mount-target --mount-target-id "${mt_id}" 2>/dev/null || true
    done
    sleep 15
    aws efs delete-file-system --file-system-id "${fs_id}" 2>/dev/null || true
  done

  echo "[7/10] Eliminando stacks de CloudFormation (agrosense-db-ec2, agrosense-vpc-iam)..."
  for stack in agrosense-db-ec2 agrosense-vpc-iam; do
    aws cloudformation delete-stack --stack-name "${stack}" 2>/dev/null || true
  done

  echo "[8/10] Eliminando alarmas CloudWatch, tópicos SNS y repositorios ECR..."
  ALARMS=$(aws cloudwatch describe-alarms --query 'MetricAlarms[*].AlarmName' --output text)
  [[ -n "${ALARMS}" ]] && aws cloudwatch delete-alarms --alarm-names ${ALARMS} 2>/dev/null || true

  for topic_arn in $(aws sns list-topics --query 'Topics[*].TopicArn' --output text); do
    [[ "${topic_arn}" == *"agrosense"* ]] && aws sns delete-topic --topic-arn "${topic_arn}" 2>/dev/null || true
  done

  for repo in agrosense-api agrosense-airflow; do
    aws ecr delete-repository --repository-name "${repo}" --force >/dev/null 2>&1 || true
  done

  echo "[9/10] Eliminando Log Groups de CloudWatch..."
  for lg in /ecs/agrosense-api /ecs/agrosense-broker /ecs/agrosense-airflow; do
    aws logs delete-log-group --log-group-name "${lg}" 2>/dev/null || true
  done

  echo "[10/10] Auditoría final post-teardown:"
  verify_resources
}

case "${MODE}" in
  verify)  verify_resources ;;
  pause)   pause_resources ;;
  destroy) destroy_resources ;;
  *)
    echo "Uso: $0 {verify|pause|destroy}"
    exit 1
    ;;
esac
