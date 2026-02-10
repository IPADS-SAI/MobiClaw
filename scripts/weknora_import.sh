#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-WeKnora-postgres-dev}"
DB_NAME="${DB_NAME:-WeKnora}"
DB_USER="${DB_USER:-postgres}"
MODELS_JSON="${MODELS_JSON:-./models_export.json}"
KB_JSON="${KB_JSON:-./knowledge_bases_export.json}"
AGENTS_JSON="${AGENTS_JSON:-./custom_agents_export.json}"
TENANTS_JSON="${TENANTS_JSON:-./tenants_export.json}"
USERS_JSON="${USERS_JSON:-./users_export.json}"

if [[ ! -f "$MODELS_JSON" ]]; then
  echo "Models file not found: $MODELS_JSON" >&2
  exit 1
fi
if [[ ! -f "$KB_JSON" ]]; then
  echo "Knowledge bases file not found: $KB_JSON" >&2
  exit 1
fi
if [[ ! -f "$AGENTS_JSON" ]]; then
  echo "Custom agents file not found: $AGENTS_JSON" >&2
  exit 1
fi
if [[ ! -f "$TENANTS_JSON" ]]; then
  echo "Tenants file not found: $TENANTS_JSON" >&2
  exit 1
fi
if [[ ! -f "$USERS_JSON" ]]; then
  echo "Users file not found: $USERS_JSON" >&2
  exit 1
fi

python - <<'PY'
import json
import os

def compact(src, dst):
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False))

compact(os.environ["MODELS_JSON"], os.environ["MODELS_JSON"] + ".compact")
compact(os.environ["KB_JSON"], os.environ["KB_JSON"] + ".compact")
compact(os.environ["AGENTS_JSON"], os.environ["AGENTS_JSON"] + ".compact")
compact(os.environ["TENANTS_JSON"], os.environ["TENANTS_JSON"] + ".compact")
compact(os.environ["USERS_JSON"], os.environ["USERS_JSON"] + ".compact")
PY

docker cp "${MODELS_JSON}.compact" "$CONTAINER_NAME:/tmp/models_export.compact.json"
docker cp "${KB_JSON}.compact" "$CONTAINER_NAME:/tmp/knowledge_bases_export.compact.json"
docker cp "${AGENTS_JSON}.compact" "$CONTAINER_NAME:/tmp/custom_agents_export.compact.json"
docker cp "${TENANTS_JSON}.compact" "$CONTAINER_NAME:/tmp/tenants_export.compact.json"
docker cp "${USERS_JSON}.compact" "$CONTAINER_NAME:/tmp/users_export.compact.json"

docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" <<'SQL'
BEGIN;
CREATE TEMP TABLE import_tenants(data jsonb);
\copy import_tenants FROM PROGRAM 'cat /tmp/tenants_export.compact.json' WITH (FORMAT csv, DELIMITER E'\x1f', QUOTE E'\b', ESCAPE E'\b');

INSERT INTO tenants (
  id, name, description, api_key, retriever_engines, status, business,
  storage_quota, storage_used, agent_config, context_config, conversation_config,
  web_search_config, created_at, updated_at, deleted_at
)
SELECT
  (t->>'id')::int,
  t->>'name',
  t->>'description',
  t->>'api_key',
  COALESCE(t->'retriever_engines', '[]'::jsonb),
  COALESCE(t->>'status', 'active'),
  COALESCE(t->>'business', ''),
  COALESCE((t->>'storage_quota')::bigint, 10737418240),
  COALESCE((t->>'storage_used')::bigint, 0),
  t->'agent_config',
  t->'context_config',
  t->'conversation_config',
  t->'web_search_config',
  NULLIF(t->>'created_at', '')::timestamptz,
  NULLIF(t->>'updated_at', '')::timestamptz,
  NULL
FROM jsonb_array_elements((SELECT data FROM import_tenants)) AS t
ON CONFLICT (id) DO UPDATE SET
  name=EXCLUDED.name,
  description=EXCLUDED.description,
  api_key=EXCLUDED.api_key,
  retriever_engines=EXCLUDED.retriever_engines,
  status=EXCLUDED.status,
  business=EXCLUDED.business,
  storage_quota=EXCLUDED.storage_quota,
  storage_used=EXCLUDED.storage_used,
  agent_config=EXCLUDED.agent_config,
  context_config=EXCLUDED.context_config,
  conversation_config=EXCLUDED.conversation_config,
  web_search_config=EXCLUDED.web_search_config,
  updated_at=EXCLUDED.updated_at,
  deleted_at=NULL;

COMMIT;
SQL

docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" <<'SQL'
BEGIN;
CREATE TEMP TABLE import_users(data jsonb);
\copy import_users FROM PROGRAM 'cat /tmp/users_export.compact.json' WITH (FORMAT csv, DELIMITER E'\x1f', QUOTE E'\b', ESCAPE E'\b');

INSERT INTO users (
  id, username, email, password_hash, avatar, tenant_id, is_active,
  can_access_all_tenants, created_at, updated_at, deleted_at
)
SELECT
  u->>'id',
  u->>'username',
  u->>'email',
  u->>'password_hash',
  COALESCE(u->>'avatar', ''),
  (u->>'tenant_id')::int,
  COALESCE((u->>'is_active')::boolean, true),
  COALESCE((u->>'can_access_all_tenants')::boolean, false),
  NULLIF(u->>'created_at', '')::timestamptz,
  NULLIF(u->>'updated_at', '')::timestamptz,
  NULL
FROM jsonb_array_elements((SELECT data FROM import_users)) AS u
ON CONFLICT (email) DO UPDATE SET
  username=EXCLUDED.username,
  password_hash=EXCLUDED.password_hash,
  avatar=EXCLUDED.avatar,
  tenant_id=EXCLUDED.tenant_id,
  is_active=EXCLUDED.is_active,
  can_access_all_tenants=EXCLUDED.can_access_all_tenants,
  updated_at=EXCLUDED.updated_at,
  deleted_at=NULL;

COMMIT;
SQL

docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" <<'SQL'
BEGIN;
CREATE TEMP TABLE import_models(data jsonb);
\copy import_models FROM PROGRAM 'cat /tmp/models_export.compact.json' WITH (FORMAT csv, DELIMITER E'\x1f', QUOTE E'\b', ESCAPE E'\b');

INSERT INTO models (
  id, tenant_id, name, type, source, description,
  parameters, is_default, status, created_at, updated_at, is_builtin, deleted_at
)
SELECT
  m->>'id',
  (m->>'tenant_id')::int,
  m->>'name',
  m->>'type',
  m->>'source',
  m->>'description',
  COALESCE(m->'parameters','{}'::jsonb),
  COALESCE((m->>'is_default')::boolean, false),
  COALESCE(m->>'status','active'),
  NULLIF(m->>'created_at','')::timestamptz,
  NULLIF(m->>'updated_at','')::timestamptz,
  COALESCE((m->>'is_builtin')::boolean, false),
  NULL
FROM jsonb_array_elements((SELECT data FROM import_models)) AS m
ON CONFLICT (id) DO UPDATE SET
  tenant_id=EXCLUDED.tenant_id,
  name=EXCLUDED.name,
  type=EXCLUDED.type,
  source=EXCLUDED.source,
  description=EXCLUDED.description,
  parameters=EXCLUDED.parameters,
  is_default=EXCLUDED.is_default,
  status=EXCLUDED.status,
  updated_at=EXCLUDED.updated_at,
  is_builtin=EXCLUDED.is_builtin,
  deleted_at=NULL;

COMMIT;
SQL

docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" <<'SQL'
BEGIN;
CREATE TEMP TABLE import_kb(data jsonb);
\copy import_kb FROM PROGRAM 'cat /tmp/knowledge_bases_export.compact.json' WITH (FORMAT csv, DELIMITER E'\x1f', QUOTE E'\b', ESCAPE E'\b');

INSERT INTO knowledge_bases (
  id, name, description, tenant_id, type, is_temporary,
  chunking_config, image_processing_config, embedding_model_id, summary_model_id,
  cos_config, vlm_config, extract_config, faq_config, question_generation_config,
  created_at, updated_at, deleted_at
)
SELECT
  kb->>'id',
  kb->>'name',
  kb->>'description',
  (kb->>'tenant_id')::int,
  COALESCE(kb->>'type','document'),
  COALESCE((kb->>'is_temporary')::boolean, false),
  COALESCE(kb->'chunking_config','{}'::jsonb),
  COALESCE(kb->'image_processing_config','{}'::jsonb),
  kb->>'embedding_model_id',
  kb->>'summary_model_id',
  COALESCE(kb->'cos_config','{}'::jsonb),
  COALESCE(kb->'vlm_config','{}'::jsonb),
  kb->'extract_config',
  kb->'faq_config',
  kb->'question_generation_config',
  NULLIF(kb->>'created_at','')::timestamptz,
  NULLIF(kb->>'updated_at','')::timestamptz,
  NULL
FROM jsonb_array_elements((SELECT data FROM import_kb)) AS kb
ON CONFLICT (id) DO UPDATE SET
  name=EXCLUDED.name,
  description=EXCLUDED.description,
  tenant_id=EXCLUDED.tenant_id,
  type=EXCLUDED.type,
  is_temporary=EXCLUDED.is_temporary,
  chunking_config=EXCLUDED.chunking_config,
  image_processing_config=EXCLUDED.image_processing_config,
  embedding_model_id=EXCLUDED.embedding_model_id,
  summary_model_id=EXCLUDED.summary_model_id,
  cos_config=EXCLUDED.cos_config,
  vlm_config=EXCLUDED.vlm_config,
  extract_config=EXCLUDED.extract_config,
  faq_config=EXCLUDED.faq_config,
  question_generation_config=EXCLUDED.question_generation_config,
  updated_at=EXCLUDED.updated_at,
  deleted_at=NULL;

COMMIT;
SQL

docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" <<'SQL'
BEGIN;
CREATE TEMP TABLE import_agents(data jsonb);
\copy import_agents FROM PROGRAM 'cat /tmp/custom_agents_export.compact.json' WITH (FORMAT csv, DELIMITER E'\x1f', QUOTE E'\b', ESCAPE E'\b');

INSERT INTO custom_agents (
  id, name, description, avatar, is_builtin, tenant_id, created_by,
  config, created_at, updated_at, deleted_at
)
SELECT
  a->>'id',
  a->>'name',
  a->>'description',
  a->>'avatar',
  COALESCE((a->>'is_builtin')::boolean, false),
  (a->>'tenant_id')::int,
  a->>'created_by',
  COALESCE(a->'config','{}'::jsonb),
  NULLIF(a->>'created_at','')::timestamptz,
  NULLIF(a->>'updated_at','')::timestamptz,
  NULL
FROM jsonb_array_elements((SELECT data FROM import_agents)) AS a
ON CONFLICT (id, tenant_id) DO UPDATE SET
  name=EXCLUDED.name,
  description=EXCLUDED.description,
  avatar=EXCLUDED.avatar,
  is_builtin=EXCLUDED.is_builtin,
  created_by=EXCLUDED.created_by,
  config=EXCLUDED.config,
  updated_at=EXCLUDED.updated_at,
  deleted_at=NULL;

COMMIT;
SQL

models_count=$(docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c "SELECT COUNT(*) FROM models WHERE deleted_at IS NULL;")
kb_count=$(docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c "SELECT COUNT(*) FROM knowledge_bases WHERE deleted_at IS NULL;")
agents_count=$(docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c "SELECT COUNT(*) FROM custom_agents WHERE deleted_at IS NULL;")
tenants_count=$(docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c "SELECT COUNT(*) FROM tenants WHERE deleted_at IS NULL;")
users_count=$(docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -At -c "SELECT COUNT(*) FROM users WHERE deleted_at IS NULL;")
echo "Import completed. tenants=$tenants_count users=$users_count models=$models_count knowledge_bases=$kb_count custom_agents=$agents_count"
