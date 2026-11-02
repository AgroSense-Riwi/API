#!/usr/bin/env bash
# ==============================================================================
# AgroSense Café — Arma el secreto <prefix>/rabbitmq-url a partir de
# <prefix>/rabbitmq-user y <prefix>/rabbitmq-password, apuntando al broker de
# ECS (Cloud Map) y al vhost /agrosense. No imprime credenciales.
#
# Uso:
#   ./infra/aws/set-rabbitmq-url.sh
#   BROKER_HOST=otro.host ./infra/aws/set-rabbitmq-url.sh
#
# Después hay que forzar un nuevo despliegue de la API para que lea el valor.
# ==============================================================================
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-2}"
PREFIX="${SECRETS_PREFIX:-agrosense/prod}"
export MSYS_NO_PATHCONV=1
BROKER_HOST="${BROKER_HOST:-broker.agrosense.local}"
VHOST_ENC="${VHOST_ENC:-%2Fagrosense}"

get() {
  aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "$PREFIX/$1" \
    --query SecretString --output text
}
# Codifica para URL (RFC 3986) sin depender de Python.
urlenc() {
  local s="$1" out="" c i
  for ((i = 0; i < ${#s}; i++)); do
    c="${s:i:1}"
    case "$c" in
      [A-Za-z0-9._~-]) out+="$c" ;;
      *) out+="$(printf '%%%02X' "'$c")" ;;
    esac
  done
  printf '%s' "$out"
}

user="$(get rabbitmq-user)"
pass="$(get rabbitmq-password)"
if [[ "$user" == \{* || "$pass" == \{* ]]; then
  echo "rabbitmq-user o rabbitmq-password está en formato JSON; este script espera texto plano." >&2
  exit 1
fi

url="amqp://$(urlenc "$user"):$(urlenc "$pass")@${BROKER_HOST}:5672/${VHOST_ENC}"
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
printf '%s' "$url" > "$tmp"
path="$tmp"; command -v cygpath >/dev/null 2>&1 && path="$(cygpath -w "$tmp")"

aws secretsmanager put-secret-value --region "$AWS_REGION" --secret-id "$PREFIX/rabbitmq-url" \
  --secret-string "file://${path}" --query VersionId --output text >/dev/null
echo "rabbitmq-url actualizado -> amqp://***:***@${BROKER_HOST}:5672/${VHOST_ENC}"
