#!/usr/bin/env bash

set -euo pipefail

# Real E2E verification for mobiagent_server (CLI mode on :8081).
# This script will trigger real device actions.

BASE_URL="${MOBI_AGENT_BASE_URL:-http://127.0.0.1:8081}"
API_KEY="${MOBI_AGENT_API_KEY:-mobi-xxx}"
TIMEOUT_S="${MOBI_REAL_TEST_TIMEOUT:-120}"
RUN_ACTION_TEST="${MOBI_REAL_TEST_RUN_ACTION:-0}"
TASK_TEXT="${MOBI_REAL_TEST_TASK:-打开微博，进入个人中心查看昵称}"

COLLECT_RESP_FILE="${MOBI_REAL_TEST_COLLECT_RESP:-/tmp/mobi_collect_resp.json}"
ACTION_RESP_FILE="${MOBI_REAL_TEST_ACTION_RESP:-/tmp/mobi_action_resp.json}"

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[ERROR] missing command: $1" >&2
    exit 1
  fi
}

need_cmd curl
need_cmd jq
need_cmd rg

echo "== 1) Health check =="
curl -sS "${BASE_URL}/" | tee /tmp/mobi_health_resp.json | jq
MODE="$(jq -r '.mode // empty' /tmp/mobi_health_resp.json)"
if [[ "${MODE}" != "cli" ]]; then
  echo "[ERROR] expected mode=cli, got mode=${MODE}" >&2
  exit 1
fi

echo
echo "== 2) Real collect test (this triggers real device actions) =="
curl -sS -X POST "${BASE_URL}/api/v1/collect" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{
    \"task\":\"${TASK_TEXT}\",
    \"options\":{\"ocr_enabled\":true,\"timeout\":${TIMEOUT_S}}
  }" | tee "${COLLECT_RESP_FILE}" | jq

SUCCESS="$(jq -r '.success // false' "${COLLECT_RESP_FILE}")"
STATUS="$(jq -r '.data.status // empty' "${COLLECT_RESP_FILE}")"
if [[ "${SUCCESS}" != "true" || "${STATUS}" != "ok" ]]; then
  echo "[ERROR] collect did not pass: success=${SUCCESS}, status=${STATUS}" >&2
  exit 1
fi

RUN_DIR="$(jq -r '.data.data_dir // .data.run_dir // empty' "${COLLECT_RESP_FILE}")"
if [[ -z "${RUN_DIR}" ]]; then
  echo "[ERROR] run directory not found in response (.data.data_dir or .data.run_dir)" >&2
  exit 1
fi

echo
echo "== 3) Artifact directory validation =="
echo "RUN_DIR=${RUN_DIR}"
test -d "${RUN_DIR}"
ls -la "${RUN_DIR}"

test -f "${RUN_DIR}/actions.json"
test -f "${RUN_DIR}/react.json"

IMAGE_COUNT="$(find "${RUN_DIR}" -maxdepth 1 -type f | rg '/[0-9]+\.jpg$' | wc -l | tr -d ' ')"
HIER_COUNT="$(find "${RUN_DIR}" -maxdepth 1 -type f | rg '/[0-9]+\.(xml|json)$' | wc -l | tr -d ' ')"
if [[ "${IMAGE_COUNT}" -lt 1 ]]; then
  echo "[ERROR] no step image found in ${RUN_DIR}" >&2
  exit 1
fi
if [[ "${HIER_COUNT}" -lt 1 ]]; then
  echo "[ERROR] no hierarchy file (.xml/.json) found in ${RUN_DIR}" >&2
  exit 1
fi

echo
echo "== 4) actions.json/react.json key fields =="
jq '.app_name,.task_description,.action_count,.actions[0].action_index' "${RUN_DIR}/actions.json"
jq '.[0].reasoning,.[0].function.name,.[0].action_index' "${RUN_DIR}/react.json"

echo
echo "== 5) Optional future-shape assertions (non-blocking) =="
# These are for the enhanced server shape planned later.
if jq -e '.data.trajectory.images' "${COLLECT_RESP_FILE}" >/dev/null 2>&1; then
  echo "trajectory.images exists"
else
  echo "trajectory.images missing (expected until enhanced response is implemented)"
fi
if jq -e '.data.history.actions' "${COLLECT_RESP_FILE}" >/dev/null 2>&1; then
  echo "history.actions exists"
else
  echo "history.actions missing (expected until enhanced response is implemented)"
fi
if jq -e '.data.history.reacts' "${COLLECT_RESP_FILE}" >/dev/null 2>&1; then
  echo "history.reacts exists"
else
  echo "history.reacts missing (expected until enhanced response is implemented)"
fi
if jq -e '.data.ocr.full_text' "${COLLECT_RESP_FILE}" >/dev/null 2>&1; then
  echo "ocr.full_text exists"
else
  echo "ocr.full_text missing (expected until enhanced response is implemented)"
fi
if jq -e '.data.index_file' "${COLLECT_RESP_FILE}" >/dev/null 2>&1; then
  echo "index_file exists"
else
  echo "index_file missing (expected until enhanced response is implemented)"
fi

if [[ "${RUN_ACTION_TEST}" == "1" ]]; then
  echo
  echo "== 6) Optional action endpoint real test =="
  curl -sS -X POST "${BASE_URL}/api/v1/action" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{
      \"action_type\":\"open_app\",
      \"params\":{\"app_name\":\"微博\"},
      \"options\":{\"wait_for_completion\":true,\"timeout\":${TIMEOUT_S}}
    }" | tee "${ACTION_RESP_FILE}" | jq
fi

echo
echo "[PASS] Real CLI collect validation passed."
echo "collect response: ${COLLECT_RESP_FILE}"
echo "run dir: ${RUN_DIR}"
