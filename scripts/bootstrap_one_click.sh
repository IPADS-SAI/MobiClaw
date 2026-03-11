#!/usr/bin/env bash
set -eEuo pipefail

# 一键启动脚本：
# 1) 拉取代码/子模块
# 2) 启动 WeKnora 基础设施、后端、前端（可选）、rerank（可选）
# 3) 导入配置、启动 mobiagent_server、运行 demo
# 4) 若任一步骤失败，自动回滚已启动模块并释放资源

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
PID_DIR="$ROOT_DIR/tmp"

WEKNORA_DIR="$ROOT_DIR/WeKnora"
WEKNORA_BACKEND_LOG="$LOG_DIR/weknora-app.log"
WEKNORA_FRONTEND_LOG="$LOG_DIR/weknora-frontend.log"
WEKNORA_RERANK_LOG="$LOG_DIR/weknora-rerank.log"
MOBI_SERVER_LOG="$LOG_DIR/mobiagent-server.log"

SKIP_PULL="${SKIP_PULL:-0}"
SKIP_IMPORT="${SKIP_IMPORT:-0}"
SKIP_FRONTEND="${SKIP_FRONTEND:-0}"
SKIP_RERANK="${SKIP_RERANK:-0}"
PRE_CLEANUP="${PRE_CLEANUP:-0}"
WEKNORA_IMPORT_GENERATE_KEY="${WEKNORA_IMPORT_GENERATE_KEY:-0}"
WEKNORA_IMPORT_UPDATE_ENV_KEY="${WEKNORA_IMPORT_UPDATE_ENV_KEY:-1}"
WEKNORA_IMPORT_TENANT_ID="${WEKNORA_IMPORT_TENANT_ID:-}"

RERANK_PORT="${RERANK_PORT:-8001}"

# 运行态标记：用于失败时只清理本脚本启动的模块
WEKNORA_INFRA_STARTED=0
STARTUP_SUCCEEDED=0
STARTED_PID_FILES=()
CLEANUP_IN_PROGRESS=0
KNOWN_PID_FILES=(
  "$PID_DIR/weknora-app.pid"
  "$PID_DIR/weknora-frontend.pid"
  "$PID_DIR/weknora-rerank.pid"
  "$PID_DIR/mobiagent-server.pid"
)

mkdir -p "$LOG_DIR" "$PID_DIR"

log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

warn() {
  printf '[%s] [WARN] %s\n' "$(date '+%H:%M:%S')" "$*" >&2
}

die() {
  printf '[%s] [ERROR] %s\n' "$(date '+%H:%M:%S')" "$*" >&2
  if [[ "$STARTUP_SUCCEEDED" != "1" && "$CLEANUP_IN_PROGRESS" != "1" ]]; then
    cleanup_started_modules
  fi
  exit 1
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

wait_http_ready() {
  local url="$1"
  local retries="${2:-90}"
  local sleep_s="${3:-2}"
  local i
  for ((i = 1; i <= retries; i++)); do
    local code
    code="$(curl -sS -o /dev/null -w '%{http_code}' "$url" || true)"
    if [[ "$code" != "000" ]] && [[ "$code" -lt 500 ]]; then
      return 0
    fi
    sleep "$sleep_s"
  done
  return 1
}

port_is_listening() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -lnt "( sport = :$port )" 2>/dev/null | grep -Eq ":$port\\b"
    return $?
  fi
  return 1
}

assert_port_free() {
  local port="$1"
  local service_name="$2"
  if port_is_listening "$port"; then
    warn "端口 ${port} 已被占用（${service_name} 需要此端口）。占用进程信息："
    print_port_process_info "$port"
    die "请释放端口 ${port} 后重试。"
  fi
}

get_env_from_file_or_default() {
  local file="$1"
  local key="$2"
  local default_value="$3"
  if [[ ! -f "$file" ]]; then
    echo "$default_value"
    return
  fi
  local line
  line="$(grep -E "^${key}=" "$file" | tail -n1 || true)"
  if [[ -z "$line" ]]; then
    echo "$default_value"
    return
  fi
  local value="${line#*=}"
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  echo "${value:-$default_value}"
}

start_bg() {
  local pid_file="$1"
  local log_file="$2"
  shift 2
  nohup "$@" >"$log_file" 2>&1 &
  echo $! >"$pid_file"
  STARTED_PID_FILES+=("$pid_file")
  log "Started (pid=$(cat "$pid_file")) -> $*"
}

print_port_process_info() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN || true
    return
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -lntp "( sport = :$port )" || true
    return
  fi
  warn "未检测到 lsof/ss，无法打印端口进程详情。"
}

get_listen_pids_by_port() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u || true
    return
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -lntp "( sport = :$port )" 2>/dev/null | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | sort -u || true
    return
  fi
}

is_managed_process() {
  local pid="$1"
  local args cwd
  args="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"

  # 命令行或工作目录命中项目路径，则判定为脚本托管进程
  if [[ -n "$args" && ( "$args" == *"$ROOT_DIR"* || "$args" == *"$WEKNORA_DIR"* || "$args" == *"mobiagent_server"* || "$args" == *"rerank_server_bge-reranker-v2-m3.py"* ) ]]; then
    return 0
  fi
  if [[ -n "$cwd" && ( "$cwd" == "$ROOT_DIR"* || "$cwd" == "$WEKNORA_DIR"* ) ]]; then
    return 0
  fi
  return 1
}

kill_managed_port_listeners() {
  local port="$1"
  local killed=0
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      continue
    fi
    if is_managed_process "$pid"; then
      local pgid
      pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)"
      if [[ -n "$pgid" ]]; then
        log "Force cleaning managed listener on port $port via pgid=$pgid (pid=$pid)"
        kill -TERM "-$pgid" >/dev/null 2>&1 || true
        sleep 1
        if pgrep -g "$pgid" >/dev/null 2>&1; then
          kill -KILL "-$pgid" >/dev/null 2>&1 || true
        fi
      else
        log "Force cleaning managed listener on port $port via pid=$pid"
        kill -TERM "$pid" >/dev/null 2>&1 || true
        sleep 1
        if kill -0 "$pid" >/dev/null 2>&1; then
          kill -KILL "$pid" >/dev/null 2>&1 || true
        fi
      fi
      killed=1
    fi
  done < <(get_listen_pids_by_port "$port")
  return "$killed"
}

# 按 pid 文件停止进程，优先 TERM，超时后 KILL
stop_pid_from_file() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return 0
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    rm -f "$pid_file"
    return 0
  fi
  if kill -0 "$pid" >/dev/null 2>&1; then
    # 优先按进程组清理，避免只杀父进程导致子进程残留（例如 WeKnora main）
    local pgid
    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ -n "$pgid" ]]; then
      log "Stopping process group pgid=$pgid (from $pid_file, pid=$pid)"
      kill -TERM "-$pgid" >/dev/null 2>&1 || true
      for _ in {1..10}; do
        if ! pgrep -g "$pgid" >/dev/null 2>&1; then
          break
        fi
        sleep 1
      done
      if pgrep -g "$pgid" >/dev/null 2>&1; then
        warn "Process group pgid=$pgid did not stop in time, sending SIGKILL"
        kill -KILL "-$pgid" >/dev/null 2>&1 || true
      fi
    else
      log "Stopping process pid=$pid (from $pid_file)"
      kill "$pid" >/dev/null 2>&1 || true
      for _ in {1..10}; do
        if ! kill -0 "$pid" >/dev/null 2>&1; then
          break
        fi
        sleep 1
      done
      if kill -0 "$pid" >/dev/null 2>&1; then
        warn "Process pid=$pid did not stop in time, sending SIGKILL"
        kill -9 "$pid" >/dev/null 2>&1 || true
      fi
    fi
  fi
  rm -f "$pid_file"
}

verify_ports_released_after_cleanup() {
  local ports=("8080" "5173" "$RERANK_PORT" "8081")
  local lingering=0
  for p in "${ports[@]}"; do
    if port_is_listening "$p"; then
      # 先尝试清理“项目托管”的残留监听进程（兼容 pid 文件丢失场景）
      kill_managed_port_listeners "$p" || true
    fi
    if port_is_listening "$p"; then
      lingering=1
      warn "Port $p is still occupied after cleanup. Listener details:"
      print_port_process_info "$p"
    fi
  done
  if [[ "$lingering" == "1" ]]; then
    warn "Some managed ports are still occupied. You may need manual stop for non-managed processes."
  fi
}

# 统一清理：仅清理本次脚本已经启动的资源
cleanup_started_modules() {
  if [[ "$CLEANUP_IN_PROGRESS" == "1" ]]; then
    return 0
  fi
  CLEANUP_IN_PROGRESS=1
  log "Cleaning up started modules..."

  # 先停本脚本本次启动并记录的后台进程
  for pid_file in "${STARTED_PID_FILES[@]}"; do
    stop_pid_from_file "$pid_file"
  done

  # 再兜底清理历史遗留的 PID 文件（避免上一次异常退出后残留）
  for pid_file in "${KNOWN_PID_FILES[@]}"; do
    stop_pid_from_file "$pid_file"
  done

  # 再停 WeKnora 基础设施容器（若本脚本已启动）
  if [[ "$WEKNORA_INFRA_STARTED" == "1" ]]; then
    log "Stopping WeKnora infrastructure..."
    bash "$WEKNORA_DIR/scripts/dev.sh" stop || warn "Failed to stop WeKnora infrastructure cleanly"
  fi

  # 清理完成后检查关键端口是否已释放
  verify_ports_released_after_cleanup
}

# 错误/中断处理：回滚并退出
on_error() {
  local exit_code="$1"
  local line_no="$2"
  warn "Script failed at line $line_no (exit_code=$exit_code)"
  if [[ "$STARTUP_SUCCEEDED" != "1" ]]; then
    cleanup_started_modules
  fi
  exit "$exit_code"
}

on_signal() {
  local sig="$1"
  warn "Received signal $sig, stopping..."
  cleanup_started_modules
  exit 1
}

# 任何未处理错误/中断都会触发回滚
trap 'on_error $? $LINENO' ERR
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM

pre_cleanup_if_needed() {
  if [[ "$PRE_CLEANUP" != "1" ]]; then
    return 0
  fi
  log "PRE_CLEANUP=1, cleaning stale managed processes before startup..."
  for pid_file in "${KNOWN_PID_FILES[@]}"; do
    stop_pid_from_file "$pid_file"
  done
}

check_required_ports() {
  local weknora_env="$WEKNORA_DIR/.env"

  local db_port redis_port docreader_port app_port
  local minio_port minio_console_port qdrant_rest_port qdrant_grpc_port
  local neo4j_http_port neo4j_bolt_port jaeger_ui_port jaeger_otlp_grpc_port

  db_port="$(get_env_from_file_or_default "$weknora_env" "DB_PORT" "5432")"
  redis_port="$(get_env_from_file_or_default "$weknora_env" "REDIS_PORT" "6379")"
  docreader_port="$(get_env_from_file_or_default "$weknora_env" "DOCREADER_PORT" "50051")"
  app_port="$(get_env_from_file_or_default "$weknora_env" "APP_PORT" "8080")"
  minio_port="$(get_env_from_file_or_default "$weknora_env" "MINIO_PORT" "9000")"
  minio_console_port="$(get_env_from_file_or_default "$weknora_env" "MINIO_CONSOLE_PORT" "9001")"
  qdrant_rest_port="$(get_env_from_file_or_default "$weknora_env" "QDRANT_REST_PORT" "6333")"
  qdrant_grpc_port="$(get_env_from_file_or_default "$weknora_env" "QDRANT_PORT" "6334")"
  neo4j_http_port="7474"
  neo4j_bolt_port="7687"
  jaeger_ui_port="16686"
  jaeger_otlp_grpc_port="4317"

  local mobi_port
  mobi_port="$(get_env_from_file_or_default "$ROOT_DIR/.env" "MOBIAGENT_GATEWAY_PORT" "8081")"

  log "Checking required ports before startup..."
  assert_port_free "$db_port" "WeKnora PostgreSQL"
  assert_port_free "$redis_port" "WeKnora Redis"
  assert_port_free "$docreader_port" "WeKnora DocReader"
  assert_port_free "$app_port" "WeKnora Backend"
  assert_port_free "$mobi_port" "MobiAgent Gateway"
  assert_port_free "$minio_port" "WeKnora MinIO"
  assert_port_free "$minio_console_port" "WeKnora MinIO Console"
  assert_port_free "$qdrant_rest_port" "WeKnora Qdrant REST"
  assert_port_free "$qdrant_grpc_port" "WeKnora Qdrant gRPC"
  assert_port_free "$neo4j_http_port" "WeKnora Neo4j HTTP"
  assert_port_free "$neo4j_bolt_port" "WeKnora Neo4j Bolt"
  assert_port_free "$jaeger_ui_port" "WeKnora Jaeger UI"
  assert_port_free "$jaeger_otlp_grpc_port" "WeKnora Jaeger OTLP gRPC"
  if [[ "$SKIP_FRONTEND" != "1" ]]; then
    assert_port_free "5173" "WeKnora Frontend"
  fi
  if [[ "$SKIP_RERANK" != "1" ]]; then
    assert_port_free "$RERANK_PORT" "WeKnora Rerank Server"
  fi
}

require_cmd git
require_cmd curl
require_cmd uv
require_cmd docker

# --- 主流程开始 ---
cd "$ROOT_DIR"

pre_cleanup_if_needed

if [[ "$SKIP_PULL" != "1" ]]; then
  log "Pulling latest code..."
  if ! git pull --ff-only; then
    warn "git pull failed (likely local changes or non-ff). Continue with current code."
  fi
fi

log "Syncing submodules..."
git submodule update --init --recursive

if [[ ! -f "$WEKNORA_DIR/.env" ]]; then
  log "Preparing WeKnora .env from template..."
  cp "$WEKNORA_DIR/.env.example" "$WEKNORA_DIR/.env"
fi

check_required_ports

log "Starting WeKnora infrastructure..."
bash "$WEKNORA_DIR/scripts/dev.sh" start --neo4j --minio
WEKNORA_INFRA_STARTED=1

log "Starting WeKnora backend..."
start_bg "$PID_DIR/weknora-app.pid" "$WEKNORA_BACKEND_LOG" \
  bash -lc "cd \"$WEKNORA_DIR\" && ./scripts/dev.sh app"

if [[ "$SKIP_FRONTEND" != "1" ]]; then
  log "Starting WeKnora frontend..."
  start_bg "$PID_DIR/weknora-frontend.pid" "$WEKNORA_FRONTEND_LOG" \
    bash -lc "cd \"$WEKNORA_DIR\" && ./scripts/dev.sh frontend"
else
  log "Skipping WeKnora frontend by SKIP_FRONTEND=1"
fi

if [[ "$SKIP_RERANK" != "1" ]]; then
  require_cmd modelscope
  require_cmd python
  if [[ ! -d "$WEKNORA_DIR/bge-reranker-v2-m3" ]]; then
    log "Deploying rerank model: BAAI/bge-reranker-v2-m3"
    bash -lc "cd \"$WEKNORA_DIR\" && modelscope download --model BAAI/bge-reranker-v2-m3 --local_dir bge-reranker-v2-m3"
  else
    log "Rerank model directory exists, skip download: $WEKNORA_DIR/bge-reranker-v2-m3"
  fi

  log "Starting WeKnora rerank server..."
  start_bg "$PID_DIR/weknora-rerank.pid" "$WEKNORA_RERANK_LOG" \
    bash -lc "cd \"$WEKNORA_DIR\" && python rerank_server_bge-reranker-v2-m3.py"

  log "Waiting for rerank server health check..."
  if ! wait_http_ready "http://127.0.0.1:${RERANK_PORT}/" 120 2; then
    die "Rerank server did not become ready in time. Check $WEKNORA_RERANK_LOG"
  fi
else
  log "Skipping rerank deploy/start by SKIP_RERANK=1"
fi

log "Waiting for WeKnora backend health check..."
if ! wait_http_ready "http://127.0.0.1:8080/health" 120 2; then
  die "WeKnora backend did not become ready in time. Check $WEKNORA_BACKEND_LOG"
fi

if [[ "$SKIP_IMPORT" != "1" ]]; then
  log "Importing WeKnora configs..."
  log "Import options: WEKNORA_IMPORT_GENERATE_KEY=$WEKNORA_IMPORT_GENERATE_KEY WEKNORA_IMPORT_UPDATE_ENV_KEY=$WEKNORA_IMPORT_UPDATE_ENV_KEY"
  # 显式指定 env + configs，避免路径歧义；如需覆盖可直接传入同名环境变量
  ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}" \
  CONFIG_DIR="${CONFIG_DIR:-$ROOT_DIR/configs}" \
  GENERATE_API_KEY="$WEKNORA_IMPORT_GENERATE_KEY" \
  UPDATE_ENV_FILE_KEY="$WEKNORA_IMPORT_UPDATE_ENV_KEY" \
  WEKNORA_TENANT_ID="$WEKNORA_IMPORT_TENANT_ID" \
  bash "$ROOT_DIR/scripts/weknora_import.sh"
else
  log "Skipping WeKnora import by SKIP_IMPORT=1"
fi

log "Syncing Python dependencies with uv..."
uv sync

log "Starting mobiagent_server..."
start_bg "$PID_DIR/mobiagent-server.pid" "$MOBI_SERVER_LOG" \
  uv run python -m mobiagent_server.server
log "Waiting for mobiagent_server health check..."
if ! wait_http_ready "http://127.0.0.1:8081/" 60 2; then
  die "mobiagent_server did not become ready in time. Check $MOBI_SERVER_LOG"
fi

log "Running Seneschal demo..."
# 启动链路通过，后续如果 demo 失败不再做“启动失败回滚”
STARTUP_SUCCEEDED=1
uv run python app.py
