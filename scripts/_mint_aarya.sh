#!/usr/bin/env bash
# scripts/_mint_aarya.sh - Arav-side helper: mint Aarya's parent api_key.
#
# Prints the api_key Aarya pastes into stall_setup_aarya.sh.
# Uses the parent api_key from ~/.quorus/profiles/default.json.

set -euo pipefail
URL="${QUORUS_RELAY_URL:-https://quorus-relay.fly.dev}"
PROFILE="${QUORUS_PROFILE_FILE:-$HOME/.quorus/profiles/default.json}"
SUFFIX="${1:-aarya}"

PK="$(jq -r '.api_key' "$PROFILE")"
[[ "$PK" =~ ^mct_ ]] || { echo "no api_key in $PROFILE" >&2; exit 1; }

CODE=$(curl -sS --max-time 15 -o /tmp/.mint.json -w '%{http_code}' \
  -X POST -H "Authorization: Bearer $PK" -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg s "$SUFFIX" '{suffix:$s}')" \
  "$URL/v1/auth/register-agent")
if [[ "$CODE" != "200" ]]; then
  echo "register-agent returned HTTP $CODE -- prod relay is degraded" >&2
  cat /tmp/.mint.json >&2; echo >&2
  exit 1
fi
KEY="$(jq -r '.api_key' /tmp/.mint.json)"
NAME="$(jq -r '.agent_name' /tmp/.mint.json)"
echo "agent_name: $NAME"
echo "api_key:    $KEY"
echo
echo "Send the api_key line above to Aarya. She pastes it into stall_setup_aarya.sh."
