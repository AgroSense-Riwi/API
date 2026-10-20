#!/usr/bin/env bash
# ==============================================================================
# AgroSense Café — Script de Provisión de Infraestructura AWS para ECS Fargate
# ==============================================================================
# Ejecuta este script UNA SOLA VEZ para crear todos los recursos base de AWS
# necesarios antes del primer despliegue de CI/CD.
#
# Prerequisitos:
#   - AWS CLI v2 configurado (aws configure)
#   - Permisos de IAM: ECS, ECR, EFS, VPC, ALB, SecretsManager, CloudWatch Logs, IAM
#
# Uso:
#   chmod +x infra/aws/setup-ecs-infra.sh
#   ./infra/aws/setup-ecs-infra.sh
# ==============================================================================
set -euo pipefail

# Asegurar que Git Bash encuentre AWS CLI instalado en AppData si no está en PATH
if ! command -v aws &> /dev/null; then
    if [ -f "$HOME/AppData/Local/Programs/Amazon/AWSCLIV2/aws.exe" ]; then
        export PATH="$HOME/AppData/Local/Programs/Amazon/AWSCLIV2:$PATH"
    elif [ -f "/c/Program Files/Amazon/AWSCLIV2/aws.exe" ]; then
        export PATH="/c/Program Files/Amazon/AWSCLIV2:$PATH"
    fi
fi

# ─── Variables de Configuración ───────────────────────────────────────────────
AWS_REGION="${AWS_REGION:-us-east-2}"
PROJECT_NAME="agrosense"
VPC_CIDR="10.0.0.0/16"
PUBLIC_SUBNET_1_CIDR="10.0.1.0/24"
PUBLIC_SUBNET_2_CIDR="10.0.2.0/24"
PRIVATE_SUBNET_1_CIDR="10.0.3.0/24"
PRIVATE_SUBNET_2_CIDR="10.0.4.0/24"

echo "=============================================="
echo " AgroSense ECS Fargate — Infrastructure Setup"
echo " Region: ${AWS_REGION}"
echo "=============================================="

# ─── 1. Amazon ECR — Repositorio de imágenes Docker ──────────────────────────
echo ""
echo "[1/8] Creando repositorio ECR..."
aws ecr create-repository \
    --repository-name "${PROJECT_NAME}-api" \
    --region "${AWS_REGION}" \
    --image-scanning-configuration scanOnPush=true \
    --encryption-configuration encryptionType=AES256 \
    2>/dev/null || echo "  ↳ Repositorio ECR ya existe."

ECR_URI=$(aws ecr describe-repositories \
    --repository-names "${PROJECT_NAME}-api" \
    --region "${AWS_REGION}" \
    --query 'repositories[0].repositoryUri' \
    --output text)
echo "  ↳ ECR URI: ${ECR_URI}"

# ─── 2. VPC y Subnets ────────────────────────────────────────────────────────
echo ""
echo "[2/8] Creando VPC y subnets..."

VPC_ID=$(aws ec2 create-vpc \
    --cidr-block "${VPC_CIDR}" \
    --tag-specifications "ResourceType=vpc,Tags=[{Key=Name,Value=${PROJECT_NAME}-vpc}]" \
    --region "${AWS_REGION}" \
    --query 'Vpc.VpcId' \
    --output text 2>/dev/null || \
    aws ec2 describe-vpcs \
        --filters "Name=tag:Name,Values=${PROJECT_NAME}-vpc" \
        --region "${AWS_REGION}" \
        --query 'Vpcs[0].VpcId' \
        --output text)

echo "  ↳ VPC ID: ${VPC_ID}"

# Habilitar DNS hostnames en la VPC
aws ec2 modify-vpc-attribute --vpc-id "${VPC_ID}" --enable-dns-hostnames '{"Value": true}' --region "${AWS_REGION}"

# Obtener AZs disponibles
AZ1=$(aws ec2 describe-availability-zones --region "${AWS_REGION}" --query 'AvailabilityZones[0].ZoneName' --output text)
AZ2=$(aws ec2 describe-availability-zones --region "${AWS_REGION}" --query 'AvailabilityZones[1].ZoneName' --output text)

# Subnets públicas (para ALB)
PUB_SUBNET_1=$(aws ec2 create-subnet \
    --vpc-id "${VPC_ID}" --cidr-block "${PUBLIC_SUBNET_1_CIDR}" --availability-zone "${AZ1}" \
    --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=${PROJECT_NAME}-public-1}]" \
    --region "${AWS_REGION}" --query 'Subnet.SubnetId' --output text 2>/dev/null || \
    aws ec2 describe-subnets --filters "Name=tag:Name,Values=${PROJECT_NAME}-public-1" \
    --region "${AWS_REGION}" --query 'Subnets[0].SubnetId' --output text)

PUB_SUBNET_2=$(aws ec2 create-subnet \
    --vpc-id "${VPC_ID}" --cidr-block "${PUBLIC_SUBNET_2_CIDR}" --availability-zone "${AZ2}" \
    --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=${PROJECT_NAME}-public-2}]" \
    --region "${AWS_REGION}" --query 'Subnet.SubnetId' --output text 2>/dev/null || \
    aws ec2 describe-subnets --filters "Name=tag:Name,Values=${PROJECT_NAME}-public-2" \
    --region "${AWS_REGION}" --query 'Subnets[0].SubnetId' --output text)

# Subnets privadas (para ECS tasks)
PRIV_SUBNET_1=$(aws ec2 create-subnet \
    --vpc-id "${VPC_ID}" --cidr-block "${PRIVATE_SUBNET_1_CIDR}" --availability-zone "${AZ1}" \
    --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=${PROJECT_NAME}-private-1}]" \
    --region "${AWS_REGION}" --query 'Subnet.SubnetId' --output text 2>/dev/null || \
    aws ec2 describe-subnets --filters "Name=tag:Name,Values=${PROJECT_NAME}-private-1" \
    --region "${AWS_REGION}" --query 'Subnets[0].SubnetId' --output text)

PRIV_SUBNET_2=$(aws ec2 create-subnet \
    --vpc-id "${VPC_ID}" --cidr-block "${PRIVATE_SUBNET_2_CIDR}" --availability-zone "${AZ2}" \
    --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=${PROJECT_NAME}-private-2}]" \
    --region "${AWS_REGION}" --query 'Subnet.SubnetId' --output text 2>/dev/null || \
    aws ec2 describe-subnets --filters "Name=tag:Name,Values=${PROJECT_NAME}-private-2" \
    --region "${AWS_REGION}" --query 'Subnets[0].SubnetId' --output text)

echo "  ↳ Public Subnets:  ${PUB_SUBNET_1}, ${PUB_SUBNET_2}"
echo "  ↳ Private Subnets: ${PRIV_SUBNET_1}, ${PRIV_SUBNET_2}"

# ─── 3. Internet Gateway + NAT Gateway ───────────────────────────────────────
echo ""
echo "[3/8] Configurando Internet Gateway y NAT Gateway..."

IGW_ID=$(aws ec2 create-internet-gateway \
    --tag-specifications "ResourceType=internet-gateway,Tags=[{Key=Name,Value=${PROJECT_NAME}-igw}]" \
    --region "${AWS_REGION}" --query 'InternetGateway.InternetGatewayId' --output text 2>/dev/null || \
    aws ec2 describe-internet-gateways --filters "Name=tag:Name,Values=${PROJECT_NAME}-igw" \
    --region "${AWS_REGION}" --query 'InternetGateways[0].InternetGatewayId' --output text)

aws ec2 attach-internet-gateway --internet-gateway-id "${IGW_ID}" --vpc-id "${VPC_ID}" --region "${AWS_REGION}" 2>/dev/null || true

# Route table pública
PUB_RT=$(aws ec2 create-route-table \
    --vpc-id "${VPC_ID}" \
    --tag-specifications "ResourceType=route-table,Tags=[{Key=Name,Value=${PROJECT_NAME}-public-rt}]" \
    --region "${AWS_REGION}" --query 'RouteTable.RouteTableId' --output text 2>/dev/null || \
    aws ec2 describe-route-tables --filters "Name=tag:Name,Values=${PROJECT_NAME}-public-rt" \
    --region "${AWS_REGION}" --query 'RouteTables[0].RouteTableId' --output text)

aws ec2 create-route --route-table-id "${PUB_RT}" --destination-cidr-block 0.0.0.0/0 --gateway-id "${IGW_ID}" --region "${AWS_REGION}" 2>/dev/null || true
aws ec2 associate-route-table --route-table-id "${PUB_RT}" --subnet-id "${PUB_SUBNET_1}" --region "${AWS_REGION}" 2>/dev/null || true
aws ec2 associate-route-table --route-table-id "${PUB_RT}" --subnet-id "${PUB_SUBNET_2}" --region "${AWS_REGION}" 2>/dev/null || true

# Elastic IP + NAT Gateway (para que los contenedores privados puedan salir a internet)
EIP_ALLOC=$(aws ec2 allocate-address --domain vpc \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=${PROJECT_NAME}-nat-eip}]" \
    --region "${AWS_REGION}" --query 'AllocationId' --output text 2>/dev/null || \
    aws ec2 describe-addresses --filters "Name=tag:Name,Values=${PROJECT_NAME}-nat-eip" \
    --region "${AWS_REGION}" --query 'Addresses[0].AllocationId' --output text)

NAT_GW_ID=$(aws ec2 create-nat-gateway \
    --subnet-id "${PUB_SUBNET_1}" --allocation-id "${EIP_ALLOC}" \
    --tag-specifications "ResourceType=natgateway,Tags=[{Key=Name,Value=${PROJECT_NAME}-nat}]" \
    --region "${AWS_REGION}" --query 'NatGateway.NatGatewayId' --output text 2>/dev/null || \
    aws ec2 describe-nat-gateways --filter "Name=tag:Name,Values=${PROJECT_NAME}-nat" "Name=state,Values=available,pending" \
    --region "${AWS_REGION}" --query 'NatGateways[0].NatGatewayId' --output text)

echo "  ↳ NAT Gateway: ${NAT_GW_ID} (esperando a que esté disponible...)"
aws ec2 wait nat-gateway-available --nat-gateway-ids "${NAT_GW_ID}" --region "${AWS_REGION}"

# Route table privada
PRIV_RT=$(aws ec2 create-route-table \
    --vpc-id "${VPC_ID}" \
    --tag-specifications "ResourceType=route-table,Tags=[{Key=Name,Value=${PROJECT_NAME}-private-rt}]" \
    --region "${AWS_REGION}" --query 'RouteTable.RouteTableId' --output text 2>/dev/null || \
    aws ec2 describe-route-tables --filters "Name=tag:Name,Values=${PROJECT_NAME}-private-rt" \
    --region "${AWS_REGION}" --query 'RouteTables[0].RouteTableId' --output text)

aws ec2 create-route --route-table-id "${PRIV_RT}" --destination-cidr-block 0.0.0.0/0 --nat-gateway-id "${NAT_GW_ID}" --region "${AWS_REGION}" 2>/dev/null || true
aws ec2 associate-route-table --route-table-id "${PRIV_RT}" --subnet-id "${PRIV_SUBNET_1}" --region "${AWS_REGION}" 2>/dev/null || true
aws ec2 associate-route-table --route-table-id "${PRIV_RT}" --subnet-id "${PRIV_SUBNET_2}" --region "${AWS_REGION}" 2>/dev/null || true

echo "  ↳ Networking configurado correctamente."

# ─── 4. Security Groups ──────────────────────────────────────────────────────
echo ""
echo "[4/8] Creando Security Groups..."

ALB_SG=$(aws ec2 create-security-group \
    --group-name "${PROJECT_NAME}-alb-sg" \
    --description "ALB - allows HTTP/HTTPS from internet" \
    --vpc-id "${VPC_ID}" \
    --region "${AWS_REGION}" --query 'GroupId' --output text 2>/dev/null || \
    aws ec2 describe-security-groups --filters "Name=group-name,Values=${PROJECT_NAME}-alb-sg" "Name=vpc-id,Values=${VPC_ID}" \
    --region "${AWS_REGION}" --query 'SecurityGroups[0].GroupId' --output text)

aws ec2 authorize-security-group-ingress --group-id "${ALB_SG}" --protocol tcp --port 80 --cidr 0.0.0.0/0 --region "${AWS_REGION}" 2>/dev/null || true
aws ec2 authorize-security-group-ingress --group-id "${ALB_SG}" --protocol tcp --port 443 --cidr 0.0.0.0/0 --region "${AWS_REGION}" 2>/dev/null || true

API_SG=$(aws ec2 create-security-group \
    --group-name "${PROJECT_NAME}-api-sg" \
    --description "API - allows traffic from internet directly on port 8000" \
    --vpc-id "${VPC_ID}" \
    --region "${AWS_REGION}" --query 'GroupId' --output text 2>/dev/null || \
    aws ec2 describe-security-groups --filters "Name=group-name,Values=${PROJECT_NAME}-api-sg" "Name=vpc-id,Values=${VPC_ID}" \
    --region "${AWS_REGION}" --query 'SecurityGroups[0].GroupId' --output text)

aws ec2 authorize-security-group-ingress --group-id "${API_SG}" --protocol tcp --port 8000 --cidr 0.0.0.0/0 --region "${AWS_REGION}" 2>/dev/null || true

DB_SG=$(aws ec2 create-security-group \
    --group-name "${PROJECT_NAME}-db-sg" \
    --description "Database - allows Postgres from API only" \
    --vpc-id "${VPC_ID}" \
    --region "${AWS_REGION}" --query 'GroupId' --output text 2>/dev/null || \
    aws ec2 describe-security-groups --filters "Name=group-name,Values=${PROJECT_NAME}-db-sg" "Name=vpc-id,Values=${VPC_ID}" \
    --region "${AWS_REGION}" --query 'SecurityGroups[0].GroupId' --output text)

aws ec2 authorize-security-group-ingress --group-id "${DB_SG}" --protocol tcp --port 5432 --source-group "${API_SG}" --region "${AWS_REGION}" 2>/dev/null || true

BROKER_SG=$(aws ec2 create-security-group \
    --group-name "${PROJECT_NAME}-broker-sg" \
    --description "Broker - allows AMQP from API only" \
    --vpc-id "${VPC_ID}" \
    --region "${AWS_REGION}" --query 'GroupId' --output text 2>/dev/null || \
    aws ec2 describe-security-groups --filters "Name=group-name,Values=${PROJECT_NAME}-broker-sg" "Name=vpc-id,Values=${VPC_ID}" \
    --region "${AWS_REGION}" --query 'SecurityGroups[0].GroupId' --output text)

aws ec2 authorize-security-group-ingress --group-id "${BROKER_SG}" --protocol tcp --port 5672 --source-group "${API_SG}" --region "${AWS_REGION}" 2>/dev/null || true

echo "  ↳ API SG:    ${API_SG}"
echo "  ↳ DB SG:     ${DB_SG}"
echo "  ↳ Broker SG: ${BROKER_SG}"

# ─── 5. Amazon EFS (persistencia para DB y RabbitMQ) ─────────────────────────
echo ""
echo "[5/7] Creando Amazon EFS para persistencia..."

EFS_ID=$(aws efs create-file-system \
    --performance-mode generalPurpose \
    --throughput-mode bursting \
    --encrypted \
    --tags "Key=Name,Value=${PROJECT_NAME}-efs" \
    --region "${AWS_REGION}" --query 'FileSystemId' --output text 2>/dev/null || \
    aws efs describe-file-systems \
    --region "${AWS_REGION}" \
    --query "FileSystems[?Tags[?Key=='Name'&&Value=='${PROJECT_NAME}-efs']].FileSystemId | [0]" \
    --output text)

echo "  ↳ EFS ID: ${EFS_ID}"
echo "  ↳ Creando mount targets..."

EFS_SG=$(aws ec2 create-security-group \
    --group-name "${PROJECT_NAME}-efs-sg" \
    --description "EFS - allows NFS from private subnets" \
    --vpc-id "${VPC_ID}" \
    --region "${AWS_REGION}" --query 'GroupId' --output text 2>/dev/null || \
    aws ec2 describe-security-groups --filters "Name=group-name,Values=${PROJECT_NAME}-efs-sg" "Name=vpc-id,Values=${VPC_ID}" \
    --region "${AWS_REGION}" --query 'SecurityGroups[0].GroupId' --output text)

aws ec2 authorize-security-group-ingress --group-id "${EFS_SG}" --protocol tcp --port 2049 --source-group "${DB_SG}" --region "${AWS_REGION}" 2>/dev/null || true
aws ec2 authorize-security-group-ingress --group-id "${EFS_SG}" --protocol tcp --port 2049 --source-group "${BROKER_SG}" --region "${AWS_REGION}" 2>/dev/null || true

aws efs create-mount-target --file-system-id "${EFS_ID}" --subnet-id "${PRIV_SUBNET_1}" --security-groups "${EFS_SG}" --region "${AWS_REGION}" 2>/dev/null || true
aws efs create-mount-target --file-system-id "${EFS_ID}" --subnet-id "${PRIV_SUBNET_2}" --security-groups "${EFS_SG}" --region "${AWS_REGION}" 2>/dev/null || true

# ─── 6. ECS Cluster ──────────────────────────────────────────────────────────
echo ""
echo "[6/7] Creando ECS Cluster..."

aws ecs create-cluster \
    --cluster-name "${PROJECT_NAME}-cluster" \
    --capacity-providers FARGATE \
    --default-capacity-provider-strategy capacityProvider=FARGATE,weight=1 \
    --setting name=containerInsights,value=enabled \
    --region "${AWS_REGION}" 2>/dev/null || echo "  ↳ Cluster ya existe."

echo "  ↳ Cluster: ${PROJECT_NAME}-cluster"

# ─── 7. AWS Secrets Manager (plantillas) ─────────────────────────────────────
echo ""
echo "[7/7] Creando secretos en AWS Secrets Manager..."

declare -A SECRETS=(
    ["agrosense/prod/database-url"]="postgresql+asyncpg://USER:PASS@HOST:5432/agrosense_db"
    ["agrosense/prod/database-url-sync"]="postgresql+psycopg://USER:PASS@HOST:5432/agrosense_db"
    ["agrosense/prod/rabbitmq-url"]="amqp://USER:PASS@HOST:5672/"
    ["agrosense/prod/jwt-secret"]="CHANGE_ME_GENERATE_SECURE_KEY"
    ["agrosense/prod/buyer-jwt-secret"]="CHANGE_ME_GENERATE_SECURE_KEY"
    ["agrosense/prod/s3-bucket"]="agrosense-datalake-prod"
    ["agrosense/prod/postgres-user"]="agrosense_user"
    ["agrosense/prod/postgres-password"]="CHANGE_ME_GENERATE_SECURE_PASSWORD"
    ["agrosense/prod/postgres-db"]="agrosense_db"
    ["agrosense/prod/rabbitmq-user"]="agrosense_mq"
    ["agrosense/prod/rabbitmq-password"]="CHANGE_ME_GENERATE_SECURE_PASSWORD"
)

for secret_name in "${!SECRETS[@]}"; do
    aws secretsmanager create-secret \
        --name "${secret_name}" \
        --secret-string "${SECRETS[$secret_name]}" \
        --region "${AWS_REGION}" 2>/dev/null || echo "  ↳ Secreto '${secret_name}' ya existe."
done

echo "  ↳ Secretos creados. ¡IMPORTANTE! Actualiza los valores placeholder en AWS Secrets Manager."

# ─── Resumen ──────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo " ✅ Infraestructura AWS provisionada"
echo "=============================================="
echo ""
echo " VPC:            ${VPC_ID}"
echo " ECS Cluster:    ${PROJECT_NAME}-cluster"
echo " ECR Repository: ${ECR_URI}"
echo " EFS:            ${EFS_ID}"
echo ""
echo " Subnets Públicas:  ${PUB_SUBNET_1}, ${PUB_SUBNET_2}"
echo " Subnets Privadas:  ${PRIV_SUBNET_1}, ${PRIV_SUBNET_2}"
echo ""
echo " Security Groups:"
echo "   API:    ${API_SG}"
echo "   DB:     ${DB_SG}"
echo "   Broker: ${BROKER_SG}"
echo "   EFS:    ${EFS_SG}"
echo ""
echo " SIGUIENTES PASOS:"
echo "  1. Actualiza los secretos en AWS Secrets Manager con valores reales"
echo "  2. Configura los GitHub Secrets (ver .github/workflows/deploy-ecs.yml)"
echo "  3. Haz push a la rama 'main' para disparar el despliegue automático"
echo ""
echo " VALORES PARA GITHUB SECRETS:"
echo "  AWS_ACCOUNT_ID  = $(aws sts get-caller-identity --query Account --output text)"
echo "  AWS_REGION      = ${AWS_REGION}"
echo "  ECS_CLUSTER     = ${PROJECT_NAME}-cluster"
echo "  ECS_SERVICE_API = agrosense-api-service"
echo "  SUBNET_1        = ${PUB_SUBNET_1}"
echo "  SUBNET_2        = ${PUB_SUBNET_2}"
echo "  API_SG          = ${API_SG}"
echo ""
