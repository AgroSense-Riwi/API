#!/usr/bin/env bash
# ==============================================================================
# AgroSense Café — Prepara Apache Airflow + PySpark en ECS Fargate (idempotente).
#
# Crea o reutiliza:
#   - secretos que faltan en Secrets Manager (valores aleatorios, nunca se imprimen):
#       agrosense/prod/airflow-fernet-key, airflow-api-secret, airflow-admin-user,
#       airflow-admin-password, s3-bucket-raw
#   - repositorio ECR agrosense-airflow (la imagen la construye
#     .github/workflows/build-airflow-image.yml)
#   - log group /ecs/agrosense-airflow
#   - tablas Athena de la capa gold (dim_finca, dim_tiempo, fact_entregas,
#     fact_alertas) sobre s3://<bucket>/gold/ en la base agrosense_datalake
#   - task definition agrosense-airflow (infra/aws/ecs/task-definition-airflow.json)
#   - servicio ECS agrosense-airflow con DESIRED_COUNT=0 (apagado)
#
# Con RUN_GOLD_DAG=1 ejecuta además una tarea única que corre
#   airflow db migrate && airflow dags test dag_gold_star_schema
# (requiere la EC2 de PostgreSQL encendida y la imagen ya publicada en ECR).
#
# Uso:
#   ./infra/aws/setup-airflow.sh
#   RUN_GOLD_DAG=1 ./infra/aws/setup-airflow.sh
#
# Encender la UI/scheduler:  aws ecs update-service --cluster agrosense-cluster --service agrosense-airflow --desired-count 1
# Apagar:                    aws ecs update-service --cluster agrosense-cluster --service agrosense-airflow --desired-count 0
# ==============================================================================
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-2}"
PROJECT_NAME="${PROJECT_NAME:-agrosense}"
SECRET_PREFIX="${SECRET_PREFIX:-agrosense/prod}"
CLUSTER="${CLUSTER:-agrosense-cluster}"
SERVICE="${SERVICE:-agrosense-airflow}"
ECR_REPO="${ECR_REPO:-agrosense-airflow}"
LOG_GROUP="${LOG_GROUP:-/ecs/agrosense-airflow}"
DATA_BUCKET="${DATA_BUCKET:-datalakeagrosense}"
GLUE_DATABASE="${GLUE_DATABASE:-agrosense_datalake}"
ATHENA_WORKGROUP="${ATHENA_WORKGROUP:-primary}"
DESIRED_COUNT="${DESIRED_COUNT:-0}"
RUN_GOLD_DAG="${RUN_GOLD_DAG:-0}"
TAGS_KV=(Project=AgroSense-Cafe Environment=production ManagedBy=IaC Owner=Keinerdmb)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TASKDEF_TEMPLATE="$SCRIPT_DIR/ecs/task-definition-airflow.json"

export AWS_DEFAULT_REGION="$AWS_REGION"
export MSYS_NO_PATHCONV=1
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ATHENA_OUTPUT="${ATHENA_OUTPUT:-s3://aws-athena-query-results-${AWS_ACCOUNT_ID}-${AWS_REGION}-lmkcawsu/}"

ecs_tags() { local o=""; for t in "${TAGS_KV[@]}"; do o+="key=${t%%=*},value=${t#*=} "; done; echo "$o"; }
kv_tags()  { local o=""; for t in "${TAGS_KV[@]}"; do o+="Key=${t%%=*},Value=${t#*=} "; done; echo "$o"; }
winpath()  { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else echo "$1"; fi; }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
chmod 700 "$WORK"

sg_id() {
  aws ec2 describe-security-groups --filters "Name=group-name,Values=$1" "Name=vpc-id,Values=$VPC_ID" \
    --query 'SecurityGroups[0].GroupId' --output text
}

echo "[1/8] Leyendo red existente..."
VPC_ID="$(aws ec2 describe-vpcs --filters "Name=tag:Name,Values=${PROJECT_NAME}-vpc" --query 'Vpcs[0].VpcId' --output text)"
PRIV_SUBNET_1="$(aws ec2 describe-subnets --filters "Name=tag:Name,Values=${PROJECT_NAME}-private-1" --query 'Subnets[0].SubnetId' --output text)"
PRIV_SUBNET_2="$(aws ec2 describe-subnets --filters "Name=tag:Name,Values=${PROJECT_NAME}-private-2" --query 'Subnets[0].SubnetId' --output text)"
# Airflow usa el SG de la API: ya tiene acceso a PostgreSQL (5432) en la EC2.
API_SG="$(sg_id "${PROJECT_NAME}-api-sg")"
for v in VPC_ID PRIV_SUBNET_1 PRIV_SUBNET_2 API_SG; do
  [[ "${!v}" != "None" && -n "${!v}" ]] || { echo "Falta $v. Ejecuta antes setup-ecs-infra.sh"; exit 1; }
  echo "  $v=${!v}"
done

echo "[2/8] Secretos de Airflow (solo se crean los que faltan; los valores no se imprimen)..."
gen_secret() {
  case "$1" in
    airflow-fernet-key)     openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n' ;;
    airflow-api-secret)     openssl rand -hex 32 | tr -d '\n' ;;
    airflow-admin-user)     printf 'admin' ;;
    airflow-admin-password) openssl rand -base64 33 | tr -d '/+=\n' | cut -c1-32 ;;
    s3-bucket-raw)          printf '%s' "$DATA_BUCKET" ;;
  esac
}
for name in airflow-fernet-key airflow-api-secret airflow-admin-user airflow-admin-password s3-bucket-raw; do
  id="${SECRET_PREFIX}/${name}"
  if aws secretsmanager describe-secret --secret-id "$id" >/dev/null 2>&1; then
    echo "  $id ya existe"
    continue
  fi
  value_file="$WORK/value"
  (umask 077; gen_secret "$name" > "$value_file")
  aws secretsmanager create-secret --name "$id" --secret-string "file://$(winpath "$value_file")" \
    --description "AgroSense Airflow (${name})" --tags $(kv_tags) >/dev/null
  rm -f "$value_file"
  echo "  $id creado"
done

echo "[3/8] Repositorio ECR ${ECR_REPO}..."
if ! aws ecr describe-repositories --repository-names "$ECR_REPO" >/dev/null 2>&1; then
  aws ecr create-repository --repository-name "$ECR_REPO" \
    --image-scanning-configuration scanOnPush=true --tags $(kv_tags) >/dev/null
  echo "  creado"
fi
aws ecr put-lifecycle-policy --repository-name "$ECR_REPO" --lifecycle-policy-text \
  '{"rules":[{"rulePriority":1,"description":"Conservar las 5 imagenes mas recientes","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":5},"action":{"type":"expire"}}]}' >/dev/null
IMAGE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}:latest"
echo "  $IMAGE_URI"

echo "[4/8] Log group ${LOG_GROUP}..."
aws logs create-log-group --log-group-name "$LOG_GROUP" --tags "$(IFS=,; echo "${TAGS_KV[*]}")" 2>/dev/null || true
aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 14

echo "[5/8] Tablas Athena de la capa gold en ${GLUE_DATABASE}..."
athena() {
  local qid st
  qid="$(aws athena start-query-execution --work-group "$ATHENA_WORKGROUP" \
    --query-string "$1" --result-configuration "OutputLocation=${ATHENA_OUTPUT}" \
    --query QueryExecutionId --output text)"
  for _ in $(seq 1 60); do
    st="$(aws athena get-query-execution --query-execution-id "$qid" \
      --query QueryExecution.Status.State --output text)"
    case "$st" in
      SUCCEEDED) return 0 ;;
      FAILED|CANCELLED)
        aws athena get-query-execution --query-execution-id "$qid" \
          --query QueryExecution.Status.StateChangeReason --output text
        return 1 ;;
    esac
    sleep 2
  done
  echo "Timeout en Athena ($qid)"; return 1
}
declare -A GOLD_COLUMNS=(
  [dim_finca]="farm_id STRING, farm_code STRING, farm_name STRING, municipality_name STRING, altitude_meters DOUBLE, total_area_hectares DOUBLE, latitude DOUBLE, longitude DOUBLE"
  [dim_tiempo]="date_key INT, full_date STRING, year INT, month INT, day INT, quarter INT, day_of_week INT, is_weekend BOOLEAN"
  [fact_entregas]="delivery_id STRING, date_key INT, farm_id STRING, variety_id INT, delivered_weight_kg DOUBLE, is_organic BOOLEAN, batch_id STRING, batch_code STRING"
  [fact_alertas]="alert_id STRING, date_key INT, farm_id STRING, alert_type STRING, severity STRING, alert_status STRING, soil_moisture_mean_pct DOUBLE, precip_accum_72h_mm DOUBLE"
)
for table in dim_finca dim_tiempo fact_entregas fact_alertas; do
  athena "CREATE EXTERNAL TABLE IF NOT EXISTS ${GLUE_DATABASE}.${table} (${GOLD_COLUMNS[$table]}) STORED AS PARQUET LOCATION 's3://${DATA_BUCKET}/gold/${table}/' TBLPROPERTIES ('parquet.compression'='SNAPPY')"
  echo "  ${GLUE_DATABASE}.${table} -> s3://${DATA_BUCKET}/gold/${table}/"
done

echo "[6/8] Registrando task definition..."
sed -e "s|\${AWS_ACCOUNT_ID}|${AWS_ACCOUNT_ID}|g" \
    -e "s|\${AWS_REGION}|${AWS_REGION}|g" \
    -e "s|\${AIRFLOW_IMAGE}|${IMAGE_URI}|g" \
    "$TASKDEF_TEMPLATE" > "$WORK/taskdef.json"
# ARN completo (con sufijo aleatorio) de cada secreto: con un ARN parcial, Secrets Manager
# toma un nombre terminado en "-" + 6 caracteres (p. ej. airflow-api-secret) como sufijo y
# la tarea falla con ResourceNotFoundException. describe-secret no lee el valor.
for name in $(grep -o "secret:${SECRET_PREFIX}/[A-Za-z0-9_-]*\"" "$WORK/taskdef.json" \
    | sed -e 's/^secret://' -e 's/"$//' | sort -u); do
  arn="$(aws secretsmanager describe-secret --secret-id "$name" --query ARN --output text)"
  sed -i "s|arn:aws:secretsmanager:${AWS_REGION}:${AWS_ACCOUNT_ID}:secret:${name}\"|${arn}\"|g" \
    "$WORK/taskdef.json"
done
TASKDEF_ARN="$(aws ecs register-task-definition --cli-input-json "file://$(winpath "$WORK/taskdef.json")" \
  --tags $(ecs_tags) --query taskDefinition.taskDefinitionArn --output text)"
echo "  $TASKDEF_ARN"

NETWORK="awsvpcConfiguration={subnets=[$PRIV_SUBNET_1,$PRIV_SUBNET_2],securityGroups=[$API_SG],assignPublicIp=DISABLED}"

echo "[7/8] Servicio ECS ${SERVICE} (desired-count=${DESIRED_COUNT})..."
status="$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'services[0].status' --output text 2>/dev/null || echo None)"
if [[ "$status" == "ACTIVE" ]]; then
  aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
    --task-definition "$TASKDEF_ARN" --desired-count "$DESIRED_COUNT" >/dev/null
else
  aws ecs create-service --cluster "$CLUSTER" --service-name "$SERVICE" \
    --task-definition "$TASKDEF_ARN" --desired-count "$DESIRED_COUNT" \
    --launch-type FARGATE --platform-version LATEST \
    --network-configuration "$NETWORK" \
    --deployment-configuration 'maximumPercent=100,minimumHealthyPercent=0' \
    --propagate-tags SERVICE --tags $(ecs_tags) >/dev/null
fi

if [[ "$RUN_GOLD_DAG" != "1" ]]; then
  echo "[8/8] Listo. Para probar el DAG gold: RUN_GOLD_DAG=1 $0"
  exit 0
fi

echo "[8/8] Tarea única: airflow dags test dag_gold_star_schema..."
cat > "$WORK/overrides.json" <<'JSON'
{"containerOverrides":[{"name":"airflow-standalone-scheduler","command":["bash","-c","airflow db migrate >/dev/null && airflow dags test dag_gold_star_schema"]}]}
JSON
TASK_ARN="$(aws ecs run-task --cluster "$CLUSTER" --task-definition "$TASKDEF_ARN" \
  --launch-type FARGATE --network-configuration "$NETWORK" \
  --overrides "file://$(winpath "$WORK/overrides.json")" --started-by setup-airflow \
  --query 'tasks[0].taskArn' --output text)"
echo "  $TASK_ARN"
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  || aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK_ARN"
EXIT_CODE="$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --query 'tasks[0].containers[0].exitCode' --output text)"
echo "  exitCode=${EXIT_CODE}  (logs: ${LOG_GROUP}, prefijo airflow/)"
[[ "$EXIT_CODE" == "0" ]]
