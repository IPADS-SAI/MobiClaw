#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-WeKnora-postgres-dev}"
DB_NAME="${DB_NAME:-WeKnora}"
DB_USER="${DB_USER:-postgres}"
OUT_DIR="${OUT_DIR:-.}"

mkdir -p "$OUT_DIR"

format_json_file() {
  local path="$1"
  python - "$path" <<'PY'
import json
import pathlib
import sys

p = pathlib.Path(sys.argv[1])
data = json.loads(p.read_text(encoding="utf-8"))
p.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
PY
}

MODELS_OUT="${OUT_DIR%/}/models_export.json"
KB_OUT="${OUT_DIR%/}/knowledge_bases_export.json"
AGENTS_OUT="${OUT_DIR%/}/custom_agents_export.json"
TENANTS_OUT="${OUT_DIR%/}/tenants_export.json"
USERS_OUT="${OUT_DIR%/}/users_export.json"

docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c \
  "WITH m AS (SELECT id, tenant_id, name, type, source, description, parameters, is_default, status, created_at, updated_at, is_builtin FROM models WHERE deleted_at IS NULL ORDER BY name) SELECT CASE WHEN COUNT(*)=0 THEN '[]' ELSE jsonb_pretty(jsonb_agg(to_jsonb(m))) END FROM m;" \
  > "$MODELS_OUT"
format_json_file "$MODELS_OUT"

docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c \
  "WITH kb AS (SELECT id, name, description, tenant_id, type, is_temporary, embedding_model_id, summary_model_id, chunking_config, image_processing_config, vlm_config, cos_config, extract_config, faq_config, question_generation_config, created_at, updated_at FROM knowledge_bases WHERE deleted_at IS NULL ORDER BY name) SELECT CASE WHEN COUNT(*)=0 THEN '[]' ELSE jsonb_pretty(jsonb_agg(to_jsonb(kb))) END FROM kb;" \
  > "$KB_OUT"
format_json_file "$KB_OUT"

docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c \
  "WITH t AS (SELECT id, name, description, api_key, retriever_engines, status, business, storage_quota, storage_used, agent_config, context_config, conversation_config, web_search_config, created_at, updated_at FROM tenants WHERE deleted_at IS NULL ORDER BY id) SELECT CASE WHEN COUNT(*)=0 THEN '[]' ELSE jsonb_pretty(jsonb_agg(to_jsonb(t))) END FROM t;" \
  > "$TENANTS_OUT"
format_json_file "$TENANTS_OUT"

docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c \
  "WITH u AS (SELECT id, username, email, password_hash, avatar, tenant_id, is_active, can_access_all_tenants, created_at, updated_at FROM users WHERE deleted_at IS NULL ORDER BY created_at) SELECT CASE WHEN COUNT(*)=0 THEN '[]' ELSE jsonb_pretty(jsonb_agg(to_jsonb(u))) END FROM u;" \
  > "$USERS_OUT"
format_json_file "$USERS_OUT"

echo "Exported: $MODELS_OUT"
echo "Exported: $KB_OUT"
echo "Exported: $TENANTS_OUT"
echo "Exported: $USERS_OUT"

if [[ -n "${WEKNORA_BASE_URL:-}" && -n "${WEKNORA_API_KEY:-}" ]]; then
  if curl -sS -H "X-API-Key: ${WEKNORA_API_KEY}" \
      "${WEKNORA_BASE_URL%/}/api/v1/agents" \
      | python - <<'PY' > "$AGENTS_OUT"
import json, sys
raw = sys.stdin.read()
data = json.loads(raw) if raw.strip() else []
agents = data.get("data") if isinstance(data, dict) else data
if agents is None:
    agents = []
print(json.dumps(agents, ensure_ascii=False, indent=2))
PY
  then
    format_json_file "$AGENTS_OUT"
    echo "Exported: $AGENTS_OUT (via API)"
  else
    echo "Agent export via API failed, falling back to DB export..." >&2
    docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c \
      "WITH a AS (SELECT id, name, description, avatar, is_builtin, tenant_id, created_by, config, created_at, updated_at FROM custom_agents WHERE deleted_at IS NULL ORDER BY name) SELECT CASE WHEN COUNT(*)=0 THEN '[]' ELSE jsonb_pretty(jsonb_agg(to_jsonb(a))) END FROM a;" \
      > "$AGENTS_OUT"
    format_json_file "$AGENTS_OUT"
    echo "Exported: $AGENTS_OUT (from DB)"
  fi
else
  docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c \
    "WITH a AS (SELECT id, name, description, avatar, is_builtin, tenant_id, created_by, config, created_at, updated_at FROM custom_agents WHERE deleted_at IS NULL ORDER BY name) SELECT CASE WHEN COUNT(*)=0 THEN '[]' ELSE jsonb_pretty(jsonb_agg(to_jsonb(a))) END FROM a;" \
    > "$AGENTS_OUT"
  format_json_file "$AGENTS_OUT"
  echo "Exported: $AGENTS_OUT (from DB)"
fi
