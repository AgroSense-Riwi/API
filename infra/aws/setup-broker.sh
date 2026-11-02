#!/usr/bin/env bash
# ==============================================================================
# AgroSense Café — Despliega el broker RabbitMQ en ECS Fargate (idempotente).
#
# Crea o reutiliza:
#   - punto de acceso EFS /rabbitmq-data (dueño uid 100 / gid 101 = usuario rabbitmq)
#   - regla NFS 2049 en agrosense-efs-sg desde agrosense-broker-sg
#   - regla AMQP 5672 en agrosense-broker-sg desde agrosense-api-sg
#   - namespace privado de Cloud Map "agrosense.local" y servicio "broker"
#     (la API llega al broker como broker.agrosense.local)
#   - task definition agrosense-broker (infra/aws/ecs/task-definition-broker.json)
#   - servicio ECS agrosense-broker en las subredes privadas
#
# El servicio se crea con DESIRED_COUNT=0 (apagado) para no gastar créditos.
# Encender:  aws ecs update-service --cluster agrosense-cluster --service agrosense-broker --desired-count 1
# Apagar:    aws ecs update-service --cluster agrosense-cluster --service agrosense-broker --desired-count 0
#
# No lee ni imprime secretos. El secreto rabbitmq-url se arma con
# infra/aws/set-rabbitmq-url.sh.
#
# Uso:
#   ./infra/aws/setup-broker.sh
# ==============================================================================
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-2}"
PROJECT_NAME="${PROJECT_NAME:-agrosense}"
CLUSTER="${CLUSTER:-agrosense-cluster}"
SERVICE="${SERVICE:-agrosense-broker}"
NAMESPACE="${NAMESPACE:-agrosense.local}"
DISCOVERY_NAME="${DISCOVERY_NAME:-broker}"
DESIRED_COUNT="${DESIRED_COUNT:-0}"
TAGS_KV=(Project=AgroSense-Cafe Environment=production ManagedBy=IaC Owner=Keinerdmb)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TASKDEF_TEMPLATE="$SCRIPT_DIR/ecs/task-definition-broker.json"

export AWS_DEFAULT_REGION="$AWS_REGION"
# Git Bash: no convertir rutas como /rabbitmq-data en argumentos.
export MSYS_NO_PATHCONV=1
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

ecs_tags() { local o=""; for t in "${TAGS_KV[@]}"; do o+="key=${t%%=*},value=${t#*=} "; done; echo "$o"; }
sd_tags()  { local o=""; for t in "${TAGS_KV[@]}"; do o+="Key=${t%%=*},Value=${t#*=} "; done; echo "$o"; }

sg_id() {
  aws ec2 describe-security-groups --filters "Name=group-name,Values=$1" "Name=vpc-id,Values=$VPC_ID" \
    --query 'SecurityGroups[0].GroupId' --output text
}

echo "[1/7] Leyendo red existente..."
VPC_ID="$(aws ec2 describe-vpcs --filters "Name=tag:Name,Values=${PROJECT_NAME}-vpc" --query 'Vpcs[0].VpcId' --output text)"
PRIV_SUBNET_1="$(aws ec2 describe-subnets --filters "Name=tag:Name,Values=${PROJECT_NAME}-private-1" --query 'Subnets[0].SubnetId' --output text)"
PRIV_SUBNET_2="$(aws ec2 describe-subnets --filters "Name=tag:Name,Values=${PROJECT_NAME}-private-2" --query 'Subnets[0].SubnetId' --output text)"
API_SG="$(sg_id "${PROJECT_NAME}-api-sg")"
BROKER_SG="$(sg_id "${PROJECT_NAME}-broker-sg")"
EFS_SG="$(sg_id "${PROJECT_NAME}-efs-sg")"
EFS_ID="$(aws efs describe-file-systems \
  --query "FileSystems[?Name=='${PROJECT_NAME}-efs'].FileSystemId | [0]" --output text)"
for v in VPC_ID PRIV_SUBNET_1 PRIV_SUBNET_2 API_SG BROKER_SG EFS_SG EFS_ID; do
  [[ "${!v}" != "None" && -n "${!v}" ]] || { echo "Falta $v. Ejecuta antes setup-ecs-infra.sh"; exit 1; }
  echo "  $v=${!v}"
done

echo "[2/7] Reglas de Security Groups..."
aws ec2 authorize-security-group-ingress --group-id "$BROKER_SG" --protocol tcp --port 5672 \
  --source-group "$API_SG" >/dev/null 2>&1 || true
aws ec2 authorize-security-group-ingress --group-id "$EFS_SG" --protocol tcp --port 2049 \
  --source-group "$BROKER_SG" >/dev/null 2>&1 || true

echo "[3/7] Punto de acceso EFS /rabbitmq-data..."
AP_ID="$(aws efs describe-access-points --file-system-id "$EFS_ID" \
  --query "AccessPoints[?RootDirectory.Path=='/rabbitmq-data'].AccessPointId | [0]" --output text)"
if [[ "$AP_ID" == "None" || -z "$AP_ID" ]]; then
  AP_ID="$(aws efs create-access-point --file-system-id "$EFS_ID" \
    --root-directory 'Path=/rabbitmq-data,CreationInfo={OwnerUid=100,OwnerGid=101,Permissions=0750}' \
    --tags "Key=Name,Value=${PROJECT_NAME}-broker-ap" $(sd_tags) \
    --query AccessPointId --output text)"
fi
echo "  AP_ID=$AP_ID"

echo "[4/7] Namespace Cloud Map ${NAMESPACE}..."
NS_ID="$(aws servicediscovery list-namespaces \
  --query "Namespaces[?Name=='${NAMESPACE}'].Id | [0]" --output text)"
if [[ "$NS_ID" == "None" || -z "$NS_ID" ]]; then
  OP_ID="$(aws servicediscovery create-private-dns-namespace --name "$NAMESPACE" --vpc "$VPC_ID" \
    --tags $(sd_tags) --query OperationId --output text)"
  for _ in $(seq 1 60); do
    st="$(aws servicediscovery get-operation --operation-id "$OP_ID" --query Operation.Status --output text)"
    [[ "$st" == "SUCCESS" ]] && break
    [[ "$st" == "FAIL" ]] && { echo "Falló la creación del namespace"; exit 1; }
    sleep 5
  done
  NS_ID="$(aws servicediscovery list-namespaces \
    --query "Namespaces[?Name=='${NAMESPACE}'].Id | [0]" --output text)"
fi
echo "  NS_ID=$NS_ID"

SD_ARN="$(aws servicediscovery list-services --filters "Name=NAMESPACE_ID,Values=$NS_ID" \
  --query "Services[?Name=='${DISCOVERY_NAME}'].Arn | [0]" --output text)"
if [[ "$SD_ARN" == "None" || -z "$SD_ARN" ]]; then
  SD_ARN="$(aws servicediscovery create-service --name "$DISCOVERY_NAME" --namespace-id "$NS_ID" \
    --dns-config 'RoutingPolicy=MULTIVALUE,DnsRecords=[{Type=A,TTL=10}]' \
    --health-check-custom-config FailureThreshold=1 \
    --tags $(sd_tags) --query Service.Arn --output text)"
fi
echo "  SD_ARN=$SD_ARN"

echo "[5/7] Registrando task definition..."
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
sed -e "s|\${AWS_ACCOUNT_ID}|${AWS_ACCOUNT_ID}|g" \
    -e "s|\${AWS_REGION}|${AWS_REGION}|g" \
    -e "s|\${EFS_FILE_SYSTEM_ID}|${EFS_ID}|g" \
    -e "s|\${EFS_BROKER_ACCESS_POINT_ID}|${AP_ID}|g" \
    "$TASKDEF_TEMPLATE" > "$tmp"
TASKDEF_PATH="$tmp"
command -v cygpath >/dev/null 2>&1 && TASKDEF_PATH="$(cygpath -w "$tmp")"
TASKDEF_ARN="$(aws ecs register-task-definition --cli-input-json "file://${TASKDEF_PATH}" \
  --tags $(ecs_tags) --query taskDefinition.taskDefinitionArn --output text)"
echo "  $TASKDEF_ARN"

echo "[6/7] Servicio ECS ${SERVICE}..."
status="$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'services[0].status' --output text 2>/dev/null || echo None)"
if [[ "$status" == "ACTIVE" ]]; then
  aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
    --task-definition "$TASKDEF_ARN" --desired-count "$DESIRED_COUNT" >/dev/null
else
  aws ecs create-service --cluster "$CLUSTER" --service-name "$SERVICE" \
    --task-definition "$TASKDEF_ARN" --desired-count "$DESIRED_COUNT" \
    --launch-type FARGATE --platform-version LATEST \
    --network-configuration "awsvpcConfiguration={subnets=[$PRIV_SUBNET_1,$PRIV_SUBNET_2],securityGroups=[$BROKER_SG],assignPublicIp=DISABLED}" \
    --service-registries "registryArn=$SD_ARN" \
    --deployment-configuration 'maximumPercent=100,minimumHealthyPercent=0' \
    --propagate-tags SERVICE --tags $(ecs_tags) >/dev/null
fi

echo "[7/7] Listo."
echo "  Host del broker para la API: ${DISCOVERY_NAME}.${NAMESPACE}:5672 (vhost /agrosense)"
echo "  Servicio con desired-count=${DESIRED_COUNT}."
