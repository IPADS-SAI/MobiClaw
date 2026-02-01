#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-WeKnora-postgres-dev}"
DB_NAME="${DB_NAME:-WeKnora}"
DB_USER="${DB_USER:-postgres}"
OUT_DIR="${OUT_DIR:-.}"

mkdir -p "$OUT_DIR"

MODELS_OUT="${OUT_DIR%/}/models_export.json"
KB_OUT="${OUT_DIR%/}/knowledge_bases_export.json"
AGENTS_OUT="${OUT_DIR%/}/custom_agents_export.json"

docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c \
  "SELECT jsonb_pretty(jsonb_agg(to_jsonb(m) - 'deleted_at')) FROM (SELECT id, tenant_id, name, type, source, description, parameters, is_default, status, created_at, updated_at, is_builtin FROM models WHERE deleted_at IS NULL ORDER BY name) m;" \
  > "$MODELS_OUT"

docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c \
  "SELECT jsonb_pretty(jsonb_agg(to_jsonb(kb) - 'deleted_at')) FROM (SELECT id, name, description, tenant_id, type, is_temporary, embedding_model_id, summary_model_id, chunking_config, image_processing_config, vlm_config, cos_config, extract_config, faq_config, question_generation_config, created_at, updated_at FROM knowledge_bases WHERE deleted_at IS NULL ORDER BY name) kb;" \
  > "$KB_OUT"

echo "Exported: $MODELS_OUT"
echo "Exported: $KB_OUT"

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
    echo "Exported: $AGENTS_OUT (via API)"
  else
    echo "Agent export via API failed, falling back to DB export..." >&2
    docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c \
      "SELECT jsonb_pretty(jsonb_agg(to_jsonb(a) - 'deleted_at')) FROM (SELECT id, name, description, avatar, is_builtin, tenant_id, created_by, config, created_at, updated_at FROM custom_agents WHERE deleted_at IS NULL ORDER BY name) a;" \
      > "$AGENTS_OUT"
    echo "Exported: $AGENTS_OUT (from DB)"
  fi
else
  docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c \
    "SELECT jsonb_pretty(jsonb_agg(to_jsonb(a) - 'deleted_at')) FROM (SELECT id, name, description, avatar, is_builtin, tenant_id, created_by, config, created_at, updated_at FROM custom_agents WHERE deleted_at IS NULL ORDER BY name) a;" \
    > "$AGENTS_OUT"
  echo "Exported: $AGENTS_OUT (from DB)"
fi
