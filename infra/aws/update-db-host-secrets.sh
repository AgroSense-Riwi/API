#!/usr/bin/env bash
# ==============================================================================
# AgroSense Café — Cambia el host de PostgreSQL en las cadenas de conexión de
# Secrets Manager (p. ej. tras recrear la EC2 con cloudformation-db-ec2.yaml).
#
# Uso:
#   OLD_HOST=10.0.1.5 NEW_HOST=10.0.3.10 ./infra/aws/update-db-host-secrets.sh
#
# Secretos que actualiza (si contienen @OLD_HOST:5432/):
#   <prefix>/database-url          etapas AWSCURRENT y PENDING-PR20 (si existe)
#   <prefix>/database-url-sync     AWSCURRENT
#   <prefix>/airflow-database-url  AWSCURRENT
# No imprime contraseñas. Después hay que forzar un nuevo despliegue de ECS para
# que las tareas lean los valores nuevos.
# ==============================================================================
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-2}"
PREFIX="${SECRETS_PREFIX:-agrosense/prod}"
OLD_HOST="${OLD_HOST:?OLD_HOST es obligatorio}"
NEW_HOST="${NEW_HOST:?NEW_HOST es obligatorio}"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

update() {
  local name="$PREFIX/$1" stage="$2" value
  if ! value="$(aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "$name" \
        --version-stage "$stage" --query SecretString --output text 2>/dev/null)"; then
    echo "  $name [$stage]: no existe, se omite"
    return
  fi
  if [[ "$value" != *"@${OLD_HOST}:5432/"* ]]; then
    echo "  $name [$stage]: no apunta a $OLD_HOST, se omite"
    return
  fi
  printf '%s' "${value//@${OLD_HOST}:5432\//@${NEW_HOST}:5432/}" > "$tmp"
  if [[ "$stage" == "AWSCURRENT" ]]; then
    aws secretsmanager put-secret-value --region "$AWS_REGION" --secret-id "$name" \
      --secret-string "file://$tmp" > /dev/null
  else
    aws secretsmanager put-secret-value --region "$AWS_REGION" --secret-id "$name" \
      --secret-string "file://$tmp" --version-stages "$stage" > /dev/null
  fi
  echo "  $name [$stage]: $OLD_HOST -> $NEW_HOST"
}

echo "Actualizando secretos en $AWS_REGION ($PREFIX)..."
update database-url AWSCURRENT
update database-url PENDING-PR20
update database-url-sync AWSCURRENT
update airflow-database-url AWSCURRENT
echo "Listo. Fuerza un despliegue: aws ecs update-service --cluster agrosense-cluster --service agrosense-api-service --force-new-deployment --region $AWS_REGION"
