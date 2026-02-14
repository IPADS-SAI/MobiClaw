#!/usr/bin/env bash
set -euo pipefail

# 一键关闭脚本：
# - 停止脚本托管的本地进程（weknora app/frontend/rerank, mobiagent_server）
# - 尝试停止 WeKnora 基础设施容器（dev.sh stop）
# - 按端口兜底清理托管进程残留

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_DIR="$ROOT_DIR/tmp"
WEKNORA_DIR="$ROOT_DIR/WeKnora"
RERANK_PORT="${RERANK_PORT:-8001}"

KNOWN_PID_FILES=(
  "$PID_DIR/weknora-app.pid"
  "$PID_DIR/weknora-frontend.pid"
  "$PID_DIR/weknora-rerank.pid"
  "$PID_DIR/mobiagent-server.pid"
)

log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

warn() {
  printf '[%s] [WARN] %s\n' "$(date '+%H:%M:%S')" "$*" >&2
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
  if [[ -n "$args" && ( "$args" == *"$ROOT_DIR"* || "$args" == *"$WEKNORA_DIR"* || "$args" == *"mobiagent_server"* || "$args" == *"rerank_server_bge-reranker-v2-m3.py"* ) ]]; then
    return 0
  fi
  if [[ -n "$cwd" && ( "$cwd" == "$ROOT_DIR"* || "$cwd" == "$WEKNORA_DIR"* ) ]]; then
    return 0
  fi
  return 1
}

kill_pid_or_group() {
  local pid="$1"
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi
  local pgid
  pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -n "$pgid" ]]; then
    kill -TERM "-$pgid" >/dev/null 2>&1 || true
    sleep 1
    if pgrep -g "$pgid" >/dev/null 2>&1; then
      kill -KILL "-$pgid" >/dev/null 2>&1 || true
    fi
  else
    kill -TERM "$pid" >/dev/null 2>&1 || true
    sleep 1
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -KILL "$pid" >/dev/null 2>&1 || true
    fi
  fi
}

stop_pid_from_file() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return 0
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "$pid" ]]; then
    log "Stopping managed process from $pid_file (pid=$pid)"
    kill_pid_or_group "$pid"
  fi
  rm -f "$pid_file"
}

kill_managed_port_listeners() {
  local port="$1"
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    if is_managed_process "$pid"; then
      log "Stopping managed listener on port $port (pid=$pid)"
      kill_pid_or_group "$pid"
    fi
  done < <(get_listen_pids_by_port "$port")
}

main() {
  log "Stopping managed local processes..."
  for pid_file in "${KNOWN_PID_FILES[@]}"; do
    stop_pid_from_file "$pid_file"
  done

  if [[ -d "$WEKNORA_DIR" ]]; then
    log "Stopping WeKnora infrastructure via dev.sh stop..."
    bash "$WEKNORA_DIR/scripts/dev.sh" stop || warn "dev.sh stop failed or services not running"
  fi

  local ports=("8080" "5173" "$RERANK_PORT" "8081")
  for p in "${ports[@]}"; do
    if port_is_listening "$p"; then
      kill_managed_port_listeners "$p"
    fi
  done

  log "Post-stop port check..."
  for p in "${ports[@]}"; do
    if port_is_listening "$p"; then
      warn "Port $p still in use:"
      print_port_process_info "$p"
    fi
  done

  log "Stop-all completed."
}

main "$@"
