#!/usr/bin/env bash
# ==============================================================================
# AgroSense Café — Prueba de humo de CA-09 / RN-11 contra la API desplegada.
#
#   ./infra/aws/ca09-smoke-test.sh CLIENT_ID_A CLIENT_ID_B
#
# Lee las api_key de KEYS_FILE (lo genera buyer-keys.sh) y comprueba:
#   1. cada comprador obtiene su token (POST /api/v1/auth/buyer-token)      -> 200
#   2. A consulta un lote propio                                            -> 200
#   3. A consulta un lote de B que no compró (CA-09)                        -> 403
#   4. A consulta un código inexistente                                     -> 404
#   5. el listado de A no contiene lotes de B (RN-11)
# No imprime keys ni tokens.
# ==============================================================================
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-2}"
KEYS_FILE="${KEYS_FILE:-$HOME/.agrosense/buyer-keys.env}"
A="${1:?Uso: $0 CLIENT_ID_A CLIENT_ID_B}"
B="${2:?Uso: $0 CLIENT_ID_A CLIENT_ID_B}"
API_URL="${API_URL:-http://$(aws elbv2 describe-load-balancers --region "$AWS_REGION" \
  --names agrosense-alb --query 'LoadBalancers[0].DNSName' --output text)}"

key_of() { sed -n "s/^$1=//p" "$KEYS_FILE" | tail -1; }
json() { node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const v=JSON.parse(s);console.log(eval(process.argv[1]))})' "$1"; }

fail=0
check() { # nombre esperado obtenido
  if [[ "$2" == "$3" ]]; then echo "  OK   $1 ($3)"; else echo "  FAIL $1: esperado $2, obtenido $3"; fail=1; fi
}

token_of() {
  local key body
  key="$(key_of "$1")"
  [[ -n "$key" ]] || { echo "No hay key para $1 en $KEYS_FILE. Ejecuta buyer-keys.sh $1" >&2; exit 1; }
  body="$(CID="$1" KEY="$key" node -e 'console.log(JSON.stringify({client_id:process.env.CID,api_key:process.env.KEY}))')"
  curl -s -m 20 -X POST "$API_URL/api/v1/auth/buyer-token" -H 'Content-Type: application/json' \
    -d "$body" | json 'v.access_token || ""'
}

status_of() { curl -s -m 20 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $1" "$API_URL$2"; }

echo "API: $API_URL"
TA="$(token_of "$A")"; TB="$(token_of "$B")"
check "token de $A" yes "$([[ -n "$TA" ]] && echo yes || echo no)"
check "token de $B" yes "$([[ -n "$TB" ]] && echo yes || echo no)"
[[ -n "$TA" && -n "$TB" ]] || exit 1

LA="$(curl -s -m 20 -H "Authorization: Bearer $TA" "$API_URL/api/v1/traceability/batches")"
LB="$(curl -s -m 20 -H "Authorization: Bearer $TB" "$API_URL/api/v1/traceability/batches")"
own_a="$(json 'v.length ? v[0].batch_code : ""' <<<"$LA")"
codes_a="$(json 'v.map(b=>b.batch_code).join(" ")' <<<"$LA")"
foreign="$(CODES_A="$codes_a" json '(v.find(b=>!process.env.CODES_A.split(" ").includes(b.batch_code))||{}).batch_code||""' <<<"$LB")"
echo "  $A tiene $(json 'v.length' <<<"$LA") lotes; $B tiene $(json 'v.length' <<<"$LB") lotes"
[[ -n "$own_a" ]] || { echo "  FAIL $A no tiene lotes: elige compradores con lotes (buyer-keys.sh sin argumentos)"; exit 1; }
[[ -n "$foreign" ]] || { echo "  FAIL $B no tiene lotes distintos a los de $A"; exit 1; }

check "$A consulta su lote $own_a" 200 "$(status_of "$TA" "/api/v1/traceability/$own_a")"
check "CA-09: $A consulta el lote $foreign de $B" 403 "$(status_of "$TA" "/api/v1/traceability/$foreign")"
check "$A consulta un lote inexistente" 404 "$(status_of "$TA" "/api/v1/traceability/NO-EXISTE-$RANDOM")"
check "RN-11: el listado de $A no incluye $foreign" no "$([[ " $codes_a " == *" $foreign "* ]] && echo yes || echo no)"

[[ $fail -eq 0 ]] && echo "CA-09 OK" || { echo "CA-09 FALLÓ"; exit 1; }
