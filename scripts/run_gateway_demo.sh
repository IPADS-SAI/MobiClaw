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

AUTH_HEADER=()
if [[ -n "${SENESCHAL_GATEWAY_API_KEY:-}" ]]; then
  AUTH_HEADER=( -H "Authorization: Bearer ${SENESCHAL_GATEWAY_API_KEY}" )
fi

python -m seneschal.gateway_server &
GATEWAY_PID=$!

cleanup() {
  kill "$GATEWAY_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

sleep 2

echo "[1/3] Sync task"
curl -sS -X POST "${GATEWAY_URL}/api/v1/task" \
  -H "Content-Type: application/json" \
  "${AUTH_HEADER[@]}" \
  -d '{"task":"获取今日待办，并给出简要总结","async_mode":false}'

echo
echo "[2/3] Async task submit"
ASYNC_RESP="$(curl -sS -X POST "${GATEWAY_URL}/api/v1/task" \
  -H "Content-Type: application/json" \
  "${AUTH_HEADER[@]}" \
  -d '{"task":"请总结今天的任务并输出关键行动建议","async_mode":true}')"
echo "${ASYNC_RESP}"

JOB_ID="$(printf '%s' "${ASYNC_RESP}" | python -c 'import sys, json; print((json.load(sys.stdin).get("job_id") or "").strip())')"
if [[ -z "${JOB_ID}" ]]; then
  echo "[ERROR] async submit did not return job_id" >&2
  exit 1
fi

echo
echo "[3/3] Poll async job: ${JOB_ID}"
for _ in {1..20}; do
  JOB_RESP="$(curl -sS "${GATEWAY_URL}/api/v1/jobs/${JOB_ID}" "${AUTH_HEADER[@]}")"
  STATUS="$(printf '%s' "${JOB_RESP}" | python -c 'import sys, json; print((json.load(sys.stdin).get("status") or "").strip())')"
  echo "status=${STATUS}"
  if [[ "${STATUS}" == "completed" || "${STATUS}" == "failed" ]]; then
    echo "${JOB_RESP}"
    break
  fi
  sleep 1
done

echo
