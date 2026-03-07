#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

GATEWAY_HOST="${SENESCHAL_GATEWAY_HOST:-127.0.0.1}"
GATEWAY_PORT="${SENESCHAL_GATEWAY_PORT:-8090}"
GATEWAY_URL="http://${GATEWAY_HOST}:${GATEWAY_PORT}"

echo "[info] This script validates webhook callback simulation only."
echo "[info] It does not validate Feishu long-connection ingress."

python -m seneschal.gateway_server &
GATEWAY_PID=$!

cleanup() {
  kill "$GATEWAY_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

sleep 2

echo "[1/2] Feishu url_verification challenge"
CHALLENGE_PAYLOAD='{"type":"url_verification","challenge":"test_challenge_123"}'
curl -sS -X POST "${GATEWAY_URL}/api/v1/feishu/events" \
  -H "Content-Type: application/json" \
  -d "${CHALLENGE_PAYLOAD}"

echo
echo "[2/2] Feishu event callback simulation"
# content 字段符合飞书消息事件格式（JSON 字符串）
EVENT_PAYLOAD='{
  "schema": "2.0",
  "header": {"event_type": "im.message.receive_v1"},
  "event": {
    "message": {
      "chat_id": "oc_mock_chat",
      "message_id": "om_mock_message",
      "content": "{\"text\":\"请给我总结今天的重点待办\"}"
    },
    "sender": {
      "sender_id": {"open_id": "ou_mock_open_id"}
    }
  }
}'

curl -sS -X POST "${GATEWAY_URL}/api/v1/feishu/events" \
  -H "Content-Type: application/json" \
  -d "${EVENT_PAYLOAD}"

echo
echo "Feishu webhook simulation submitted."
