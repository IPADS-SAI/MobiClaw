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

GATEWAY_HOST="${MOBICLAW_GATEWAY_HOST:-127.0.0.1}"
GATEWAY_PORT="${MOBICLAW_GATEWAY_PORT:-8090}"
GATEWAY_URL="http://${GATEWAY_HOST}:${GATEWAY_PORT}"

AUTH_HEADER=()
if [[ -n "${MOBICLAW_GATEWAY_API_KEY:-}" ]]; then
  AUTH_HEADER=( -H "Authorization: Bearer ${MOBICLAW_GATEWAY_API_KEY}" )
fi

python -m mobiclaw.gateway_server &
GATEWAY_PID=$!

cleanup() {
  kill "$GATEWAY_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

sleep 2

echo "[1/3] Sync task (intelligent routing)"
curl -sS -X POST "${GATEWAY_URL}/api/v1/task" \
  -H "Content-Type: application/json" \
  "${AUTH_HEADER[@]}" \
  -d '{"task":"请先整理今日待办，再补充联网检索相关背景并给出行动建议","async_mode":false,"mode":"router"}'

echo
echo "[2/3] Async task submit"
ASYNC_RESP="$(curl -sS -X POST "${GATEWAY_URL}/api/v1/task" \
  -H "Content-Type: application/json" \
  "${AUTH_HEADER[@]}" \
  -d '{"task":"先基于手机侧信息提炼重点，再联网补充三条可执行建议，最后输出 markdown","async_mode":true,"mode":"router"}')"
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
    echo "routing_trace summary:"
    printf '%s' "${JOB_RESP}" | python -c 'import sys, json; d=json.load(sys.stdin); t=((d.get("result") or {}).get("routing_trace") or {}); print(json.dumps({"decision":t.get("decision"),"plan_source":t.get("plan_source")}, ensure_ascii=False))'
    echo "${JOB_RESP}"
    break
  fi
  sleep 1
done

echo
