#!/usr/bin/env bash
# ==============================================================================
# AgroSense Café — Credenciales de compradores (RNF-06) en el Postgres de EC2.
#
#   ./infra/aws/buyer-keys.sh                    # lista compradores y sus lotes
#   ./infra/aws/buyer-keys.sh CLIENT_ID [...]    # genera api_key + hash Argon2
#
# Por cada CLIENT_ID genera una api_key nueva (agrosense_api.cli new-buyer-key), guarda
# SOLO el hash en international_buyers.api_key_hash (vía SSM, psql como postgres) y
# escribe las keys en KEYS_FILE (por defecto ~/.agrosense/buyer-keys.env, permisos 600).
# Las keys NO se imprimen en pantalla. Entrégalas a cada comprador por un canal seguro
# y nunca las subas al repositorio (CT-02).
#
# Requiere: AWS CLI con sesión, uv, la EC2 encendida y registrada en SSM.
# ==============================================================================
set -euo pipefail
export MSYS_NO_PATHCONV=1

AWS_REGION="${AWS_REGION:-us-east-2}"
PREFIX="${SECRETS_PREFIX:-agrosense/prod}"
INSTANCE_ID="${INSTANCE_ID:-$(aws ec2 describe-instances --region "$AWS_REGION" \
  --filters Name=tag:Name,Values=agrosense-db-server Name=instance-state-name,Values=running \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)}"
KEYS_FILE="${KEYS_FILE:-$HOME/.agrosense/buyer-keys.env}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

[[ "$INSTANCE_ID" != "None" && -n "$INSTANCE_ID" ]] || { echo "La EC2 agrosense-db-server no está encendida." >&2; exit 1; }
DB_NAME="$(aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "$PREFIX/postgres-db" \
  --query SecretString --output text)"

# Ejecuta un archivo SQL en la EC2 como postgres. El SQL viaja en base64 para que el
# shell remoto no expanda los "$" de los hashes Argon2.
run_sql() {
  local b64 cid status
  b64="$(base64 -w0 < "$1")"
  cid="$(aws ssm send-command --region "$AWS_REGION" --instance-ids "$INSTANCE_ID" \
    --document-name AWS-RunShellScript --comment "agrosense buyer-keys" \
    --parameters "commands=[\"echo $b64 | base64 -d > /tmp/agrosense-q.sql\",\"runuser -u postgres -- psql -X -q -v ON_ERROR_STOP=1 -d $DB_NAME -f /tmp/agrosense-q.sql; rc=\$?; rm -f /tmp/agrosense-q.sql; exit \$rc\"]" \
    --query Command.CommandId --output text)"
  for _ in $(seq 1 30); do
    sleep 2
    status="$(aws ssm get-command-invocation --region "$AWS_REGION" --command-id "$cid" \
      --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null || echo Pending)"
    [[ "$status" == Pending || "$status" == InProgress || "$status" == Delayed ]] || break
  done
  aws ssm get-command-invocation --region "$AWS_REGION" --command-id "$cid" \
    --instance-id "$INSTANCE_ID" --query '[StandardOutputContent,StandardErrorContent]' --output text
  [[ "$status" == Success ]]
}

sql="$(mktemp)"; trap 'rm -f "$sql"' EXIT

if [[ $# -eq 0 ]]; then
  cat > "$sql" <<'SQL'
\pset footer off
SELECT b.client_id, b.buyer_name, b.is_active,
       (b.api_key_hash IS NOT NULL) AS tiene_key,
       count(c.contract_id) AS lotes
FROM international_buyers b
LEFT JOIN buyer_batch_contracts c ON c.buyer_id = b.buyer_id
GROUP BY b.buyer_id
ORDER BY lotes DESC, b.client_id
LIMIT 40;
SQL
  run_sql "$sql"
  exit 0
fi

mkdir -p "$(dirname "$KEYS_FILE")"; touch "$KEYS_FILE"; chmod 600 "$KEYS_FILE"
declare -A NEW_KEYS=()
: > "$sql"
for client_id in "$@"; do
  [[ "$client_id" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "client_id inválido: $client_id" >&2; exit 1; }
  out="$(cd "$REPO_ROOT" && uv run --quiet python -m agrosense_api.cli new-buyer-key)"
  key="$(sed -n 's/^api_key ([^)]*): //p' <<<"$out")"
  hash="$(sed -n 's/^api_key_hash ([^)]*): //p' <<<"$out")"
  [[ -n "$key" && "$hash" == \$argon2* ]] || { echo "No se pudo generar la key de $client_id" >&2; exit 1; }
  NEW_KEYS["$client_id"]="$key"
  cat >> "$sql" <<SQL
UPDATE international_buyers SET api_key_hash = '$hash', is_active = true
 WHERE client_id = '$client_id' RETURNING client_id AS actualizado;
SQL
done

if run_sql "$sql"; then
  for client_id in "${!NEW_KEYS[@]}"; do
    grep -v "^${client_id}=" "$KEYS_FILE" > "$KEYS_FILE.tmp" || true
    printf '%s=%s\n' "$client_id" "${NEW_KEYS[$client_id]}" >> "$KEYS_FILE.tmp"
    mv "$KEYS_FILE.tmp" "$KEYS_FILE"
  done
  chmod 600 "$KEYS_FILE"
  echo "Keys guardadas en $KEYS_FILE (no se muestran). Si un client_id no aparece arriba como 'actualizado', no existe en la base."
else
  echo "Falló la actualización; no se guardó ninguna key." >&2
  exit 1
fi
