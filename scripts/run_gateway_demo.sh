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

python -m seneschal.gateway_server &
GATEWAY_PID=$!

cleanup() {
  kill "$GATEWAY_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

sleep 2

curl -sS -X POST "http://${GATEWAY_HOST}:${GATEWAY_PORT}/api/v1/task" \
  -H "Content-Type: application/json" \
  -d '{"task":"获取今日待办，并给出简要总结","async_mode":false}'

echo
