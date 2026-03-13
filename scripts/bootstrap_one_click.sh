#!/usr/bin/env bash
set -eEuo pipefail

# 一键启动脚本：
# 1) 拉取代码/子模块
# 2) 启动 mobiagent_server、运行 demo
# 3) 若任一步骤失败，自动回滚已启动模块并释放资源

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
PID_DIR="$ROOT_DIR/tmp"

MOBI_SERVER_LOG="$LOG_DIR/mobiagent-server.log"

SKIP_PULL="${SKIP_PULL:-0}"
PRE_CLEANUP="${PRE_CLEANUP:-0}"

# 运行态标记：用于失败时只清理本脚本启动的模块
STARTUP_SUCCEEDED=0
STARTED_PID_FILES=()
CLEANUP_IN_PROGRESS=0
KNOWN_PID_FILES=(
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
  if [[ -n "$args" && ( "$args" == *"$ROOT_DIR"* || "$args" == *"mobiagent_server"* ) ]]; then
    return 0
  fi
  if [[ -n "$cwd" && "$cwd" == "$ROOT_DIR"* ]]; then
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
    # 优先按进程组清理，避免只杀父进程导致子进程残留
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
  local ports=("8081")
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
  local mobi_port
  mobi_port="$(get_env_from_file_or_default "$ROOT_DIR/.env" "MOBIAGENT_GATEWAY_PORT" "8081")"

  log "Checking required ports before startup..."
  assert_port_free "$mobi_port" "MobiAgent Gateway"
}

require_cmd git
require_cmd curl
require_cmd uv

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

check_required_ports

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
# 启动链路通过，后续如果 demo 失败不再做"启动失败回滚"
STARTUP_SUCCEEDED=1
uv run python app.py
