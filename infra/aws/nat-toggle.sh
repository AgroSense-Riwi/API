#!/usr/bin/env bash
# ==============================================================================
# AgroSense Café — Control de Costos NAT Gateway + Elastic IP (FinOps / CT-03)
# ==============================================================================
# Permite encender (up), apagar (down) o consultar el estado (status) del
# NAT Gateway y su Elastic IP en us-east-2 en un solo comando (~$1.20 USD/día
# de ahorro mientras el entorno de ECS está apagado).
#
# Uso:
#   ./infra/aws/nat-toggle.sh status
#   ./infra/aws/nat-toggle.sh down     # Elimina NAT Gateway + libera EIP
#   ./infra/aws/nat-toggle.sh up       # Crea EIP + NAT Gateway y actualiza agrosense-private-rt
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
        exec "$REPO_ROOT/.venv/Scripts/python.exe" "$SCRIPT_DIR/aws_ops.py" nat "${1:-status}"
    elif command -v python3 &> /dev/null; then
        exec python3 "$SCRIPT_DIR/aws_ops.py" nat "${1:-status}"
    fi
fi

AWS_REGION="${AWS_REGION:-us-east-2}"
PROJECT_NAME="${PROJECT_NAME:-agrosense}"
ACTION="${1:-status}"

export AWS_DEFAULT_REGION="${AWS_REGION}"
export MSYS_NO_PATHCONV=1

STD_TAGS="Key=Name,Value=${PROJECT_NAME}-nat Key=proyecto,Value=AgroSense-Cafe Key=ambiente,Value=production Key=responsable,Value=AgroSense-Team Key=Project,Value=AgroSense-Cafe Key=Environment,Value=production Key=ManagedBy,Value=IaC"

case "${ACTION}" in
  status)
    echo "=== Estado actual de NAT Gateway en ${AWS_REGION} ==="
    aws ec2 describe-nat-gateways \
      --filter "Name=state,Values=available,pending,deleting" \
      --query 'NatGateways[*].{ID:NatGatewayId,State:State,Subnet:SubnetId,PublicIp:NatGatewayAddresses[0].PublicIp,EIP:NatGatewayAddresses[0].AllocationId}' \
      --output table
    ;;

  down)
    echo "=== Apagando NAT Gateway y liberando Elastic IP (${AWS_REGION}) ==="
    NAT_IDS=$(aws ec2 describe-nat-gateways \
      --filter "Name=state,Values=available,pending" \
      --query 'NatGateways[*].NatGatewayId' --output text)

    if [[ -z "${NAT_IDS}" || "${NAT_IDS}" == "None" ]]; then
      echo "  ↳ No hay NAT Gateways activos."
    else
      for nat_id in ${NAT_IDS}; do
        EIP_ALLOC=$(aws ec2 describe-nat-gateways --nat-gateway-ids "${nat_id}" \
          --query 'NatGateways[0].NatGatewayAddresses[0].AllocationId' --output text)
        echo "  ↳ Eliminando NAT Gateway ${nat_id}..."
        aws ec2 delete-nat-gateway --nat-gateway-id "${nat_id}" >/dev/null
        echo "  ↳ Esperando que ${nat_id} pase a 'deleted' para liberar EIP ${EIP_ALLOC}..."
        for _ in $(seq 1 30); do
          st=$(aws ec2 describe-nat-gateways --nat-gateway-ids "${nat_id}" \
            --query 'NatGateways[0].State' --output text)
          [[ "${st}" == "deleted" ]] && break
          sleep 3
        done
        if [[ -n "${EIP_ALLOC}" && "${EIP_ALLOC}" != "None" ]]; then
          aws ec2 release-address --allocation-id "${EIP_ALLOC}" 2>/dev/null || true
          echo "  ↳ Elastic IP ${EIP_ALLOC} liberada."
        fi
      done
    fi
    echo "=== ✅ NAT Gateway apagado ($0.00/h en NAT/EIP) ==="
    ;;

  up)
    echo "=== Encendiendo NAT Gateway + Elastic IP y enrutando subredes privadas ==="
    EXISTING_NAT=$(aws ec2 describe-nat-gateways \
      --filter "Name=tag:Name,Values=${PROJECT_NAME}-nat" "Name=state,Values=available,pending" \
      --query 'NatGateways[0].NatGatewayId' --output text)

    if [[ -n "${EXISTING_NAT}" && "${EXISTING_NAT}" != "None" ]]; then
      echo "  ↳ NAT Gateway ya existe: ${EXISTING_NAT}"
      NAT_GW_ID="${EXISTING_NAT}"
    else
      PUB_SUBNET_1=$(aws ec2 describe-subnets \
        --filters "Name=tag:Name,Values=${PROJECT_NAME}-public-1" \
        --query 'Subnets[0].SubnetId' --output text)

      EIP_ALLOC=$(aws ec2 allocate-address --domain vpc \
        --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=${PROJECT_NAME}-nat-eip},{Key=proyecto,Value=AgroSense-Cafe},{Key=ambiente,Value=production},{Key=responsable,Value=AgroSense-Team}]" \
        --query 'AllocationId' --output text)

      NAT_GW_ID=$(aws ec2 create-nat-gateway \
        --subnet-id "${PUB_SUBNET_1}" \
        --allocation-id "${EIP_ALLOC}" \
        --tag-specifications "ResourceType=natgateway,Tags=[{Key=Name,Value=${PROJECT_NAME}-nat},{Key=proyecto,Value=AgroSense-Cafe},{Key=ambiente,Value=production},{Key=responsable,Value=AgroSense-Team},{Key=Project,Value=AgroSense-Cafe},{Key=Environment,Value=production},{Key=ManagedBy,Value=IaC}]" \
        --query 'NatGateway.NatGatewayId' --output text)

      echo "  ↳ Esperando disponibilidad de ${NAT_GW_ID}..."
      aws ec2 wait nat-gateway-available --nat-gateway-ids "${NAT_GW_ID}"
    fi

    PRIV_RT=$(aws ec2 describe-route-tables \
      --filters "Name=tag:Name,Values=${PROJECT_NAME}-private-rt" \
      --query 'RouteTables[0].RouteTableId' --output text)

    aws ec2 replace-route --route-table-id "${PRIV_RT}" \
      --destination-cidr-block 0.0.0.0/0 --nat-gateway-id "${NAT_GW_ID}" 2>/dev/null || \
    aws ec2 create-route --route-table-id "${PRIV_RT}" \
      --destination-cidr-block 0.0.0.0/0 --nat-gateway-id "${NAT_GW_ID}" >/dev/null

    echo "=== ✅ NAT Gateway ${NAT_GW_ID} activo y enrutado en ${PRIV_RT} ==="
    ;;

  *)
    echo "Uso: $0 {status|up|down}"
    exit 1
    ;;
esac
