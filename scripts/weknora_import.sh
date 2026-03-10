#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
CONFIG_DIR="${CONFIG_DIR:-$ROOT_DIR/configs}"
CONTAINER_NAME="${CONTAINER_NAME:-WeKnora-postgres-dev}"
DB_NAME="${DB_NAME:-WeKnora}"
DB_USER="${DB_USER:-postgres}"
MODELS_JSON="${MODELS_JSON:-$CONFIG_DIR/models_export.json}"
KB_JSON="${KB_JSON:-$CONFIG_DIR/knowledge_bases_export.json}"
AGENTS_JSON="${AGENTS_JSON:-$CONFIG_DIR/custom_agents_export.json}"
TENANTS_JSON="${TENANTS_JSON:-$CONFIG_DIR/tenants_export.json}"
USERS_JSON="${USERS_JSON:-$CONFIG_DIR/users_export.json}"
GENERATE_API_KEY="${GENERATE_API_KEY:-0}"
UPDATE_ENV_FILE_KEY="${UPDATE_ENV_FILE_KEY:-1}"
WEKNORA_TENANT_ID="${WEKNORA_TENANT_ID:-}"
GENERATED_API_KEY=""

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  bash ./scripts/weknora_import.sh

Environment variables:
  ENV_FILE        Path to env file used for ${VAR} substitution (default: ./.env)
  CONFIG_DIR      Directory of template json files (default: ./configs)
  MODELS_JSON     Override models json path
  KB_JSON         Override knowledge bases json path
  AGENTS_JSON     Override custom agents json path
  TENANTS_JSON    Override tenants json path
  USERS_JSON      Override users json path
  CONTAINER_NAME  Postgres container name (default: WeKnora-postgres-dev)
  DB_NAME         Database name (default: WeKnora)
  DB_USER         Database user (default: postgres)
  GENERATE_API_KEY  Set to 1 to generate a fresh tenant API key before import
  UPDATE_ENV_FILE_KEY Set to 1 to write generated key back into ENV_FILE (default: 1)
  WEKNORA_TENANT_ID  Tenant ID used for key generation (default: first tenant id from TENANTS_JSON)

Required for GENERATE_API_KEY=1:
  TENANT_AES_KEY   Must match current WeKnora runtime TENANT_AES_KEY
EOF
  exit 0
fi

# 兼容单数文件名 custom_agent_export.json
if [[ ! -f "$AGENTS_JSON" && -f "$CONFIG_DIR/custom_agent_export.json" ]]; then
  AGENTS_JSON="$CONFIG_DIR/custom_agent_export.json"
fi

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

echo "[weknora_import] ENV_FILE=$ENV_FILE"
echo "[weknora_import] CONFIG_DIR=$CONFIG_DIR"
echo "[weknora_import] MODELS_JSON=$MODELS_JSON"
echo "[weknora_import] KB_JSON=$KB_JSON"
echo "[weknora_import] AGENTS_JSON=$AGENTS_JSON"
echo "[weknora_import] TENANTS_JSON=$TENANTS_JSON"
echo "[weknora_import] USERS_JSON=$USERS_JSON"
echo "[weknora_import] GENERATE_API_KEY=$GENERATE_API_KEY"
echo "[weknora_import] UPDATE_ENV_FILE_KEY=$UPDATE_ENV_FILE_KEY"

if [[ "$GENERATE_API_KEY" == "1" ]]; then
  if ! command -v go >/dev/null 2>&1; then
    echo "go is required when GENERATE_API_KEY=1" >&2
    exit 1
  fi

  if [[ -z "${TENANT_AES_KEY:-}" ]]; then
    if [[ -f "$ENV_FILE" ]]; then
      # shellcheck disable=SC2016
      TENANT_AES_KEY="$(grep -E '^[[:space:]]*(export[[:space:]]+)?TENANT_AES_KEY=' "$ENV_FILE" | tail -n1 | sed -E 's/^[[:space:]]*(export[[:space:]]+)?TENANT_AES_KEY=//; s/^["\x27]//; s/["\x27]$//')"
      export TENANT_AES_KEY
    fi
  fi

  if [[ -z "${TENANT_AES_KEY:-}" ]]; then
    echo "TENANT_AES_KEY is required for GENERATE_API_KEY=1" >&2
    exit 1
  fi

  if [[ -z "$WEKNORA_TENANT_ID" ]]; then
    WEKNORA_TENANT_ID="$(TENANTS_JSON="$TENANTS_JSON" python - <<'PY'
import json
import os
from pathlib import Path
path = Path(os.environ['TENANTS_JSON'])
data = json.loads(path.read_text(encoding='utf-8'))
if not isinstance(data, list) or not data:
    raise SystemExit('TENANTS_JSON must contain at least one tenant object')
tenant_id = data[0].get('id')
if tenant_id is None:
    raise SystemExit('TENANTS_JSON first object missing id')
print(str(int(tenant_id)))
PY
)"
  fi

  gen_go_file="$(mktemp /tmp/weknora-keygen-XXXXXX.go)"
  cat > "$gen_go_file" <<'EOF'
package main

import (
  "crypto/aes"
  "crypto/cipher"
  "crypto/rand"
  "encoding/base64"
  "encoding/binary"
  "fmt"
  "io"
  "os"
  "strconv"
)

func main() {
  secret := []byte(os.Getenv("TENANT_AES_KEY"))
  tenantIDStr := os.Getenv("WEKNORA_TENANT_ID")
  if len(secret) != 16 && len(secret) != 24 && len(secret) != 32 {
    panic("TENANT_AES_KEY length must be 16/24/32 bytes")
  }
  tenantID, err := strconv.ParseUint(tenantIDStr, 10, 64)
  if err != nil {
    panic(err)
  }

  idBytes := make([]byte, 8)
  binary.LittleEndian.PutUint64(idBytes, tenantID)

  block, err := aes.NewCipher(secret)
  if err != nil {
    panic(err)
  }
  aesgcm, err := cipher.NewGCM(block)
  if err != nil {
    panic(err)
  }

  nonce := make([]byte, 12)
  if _, err := io.ReadFull(rand.Reader, nonce); err != nil {
    panic(err)
  }

  ciphertext := aesgcm.Seal(nil, nonce, idBytes, nil)
  combined := append(nonce, ciphertext...)
  fmt.Print("sk-" + base64.RawURLEncoding.EncodeToString(combined))
}
EOF

  GENERATED_API_KEY="$(TENANT_AES_KEY="$TENANT_AES_KEY" WEKNORA_TENANT_ID="$WEKNORA_TENANT_ID" go run "$gen_go_file")"
  rm -f "$gen_go_file"

  if [[ -z "$GENERATED_API_KEY" ]]; then
    echo "failed to generate api key" >&2
    exit 1
  fi

  export WEKNORA_API_KEY="$GENERATED_API_KEY"
  echo "[weknora_import] Generated API key for tenant_id=$WEKNORA_TENANT_ID"

  if [[ "$UPDATE_ENV_FILE_KEY" == "1" ]]; then
    if grep -qE '^[[:space:]]*(export[[:space:]]+)?WEKNORA_API_KEY=' "$ENV_FILE"; then
      sed -i -E 's#^[[:space:]]*(export[[:space:]]+)?WEKNORA_API_KEY=.*#export WEKNORA_API_KEY="'"$GENERATED_API_KEY"'"#' "$ENV_FILE"
    else
      printf '\nexport WEKNORA_API_KEY="%s"\n' "$GENERATED_API_KEY" >> "$ENV_FILE"
    fi
    echo "[weknora_import] Updated WEKNORA_API_KEY in $ENV_FILE"
  fi
fi

ROOT_DIR="$ROOT_DIR" \
ENV_FILE="$ENV_FILE" \
MODELS_JSON="$MODELS_JSON" \
KB_JSON="$KB_JSON" \
AGENTS_JSON="$AGENTS_JSON" \
TENANTS_JSON="$TENANTS_JSON" \
USERS_JSON="$USERS_JSON" \
python - <<'PY'
import json
import os
import re
from pathlib import Path

VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        # shell 中已存在的值优先，不覆盖
        os.environ.setdefault(k, v)


def render_string(value: str, missing: set[str]) -> str:
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        env_val = os.environ.get(key)
        if env_val is None:
            missing.add(key)
            return m.group(0)
        return env_val
    return VAR_PATTERN.sub(repl, value)


def render_obj(obj, missing: set[str]):
    if isinstance(obj, dict):
        return {k: render_obj(v, missing) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render_obj(v, missing) for v in obj]
    if isinstance(obj, str):
        return render_string(obj, missing)
    return obj


def compact(src: str, dst: str):
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    missing: set[str] = set()
    rendered = render_obj(data, missing)
    if missing:
        raise RuntimeError(
            f"{src} has unresolved env vars: {', '.join(sorted(missing))}"
        )
    with open(dst, "w", encoding="utf-8") as f:
        f.write(json.dumps(rendered, ensure_ascii=False))

# 导入时自动加载根目录 .env，支持 ${VAR} 占位符替换
root_dir = Path(os.environ["ROOT_DIR"])
env_file = Path(os.environ["ENV_FILE"])
load_env_file(env_file)

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
  NOW(),
  NOW(),
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
  created_at=EXCLUDED.created_at,
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
  NOW(),
  NOW(),
  NULL
FROM jsonb_array_elements((SELECT data FROM import_users)) AS u
ON CONFLICT (email) DO UPDATE SET
  username=EXCLUDED.username,
  password_hash=EXCLUDED.password_hash,
  avatar=EXCLUDED.avatar,
  tenant_id=EXCLUDED.tenant_id,
  is_active=EXCLUDED.is_active,
  can_access_all_tenants=EXCLUDED.can_access_all_tenants,
  created_at=EXCLUDED.created_at,
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
  NOW(),
  NOW(),
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
  created_at=EXCLUDED.created_at,
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
  NOW(),
  NOW(),
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
  created_at=EXCLUDED.created_at,
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
  NOW(),
  NOW(),
  NULL
FROM jsonb_array_elements((SELECT data FROM import_agents)) AS a
ON CONFLICT (id, tenant_id) DO UPDATE SET
  name=EXCLUDED.name,
  description=EXCLUDED.description,
  avatar=EXCLUDED.avatar,
  is_builtin=EXCLUDED.is_builtin,
  created_by=EXCLUDED.created_by,
  config=EXCLUDED.config,
  created_at=EXCLUDED.created_at,
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

if [[ -n "$GENERATED_API_KEY" ]]; then
  echo "Generated WEKNORA_API_KEY=$GENERATED_API_KEY"
  echo "export WEKNORA_API_KEY=$GENERATED_API_KEY" >> "$ENV_FILE"
fi
