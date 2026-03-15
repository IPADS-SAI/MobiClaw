from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("mobiclaw.gateway_server")

_CHAT_SESSION_NAME_RE = re.compile(r"^(?P<prefix>\d{8}_\d{6}_\d{6})-(?P<storage_context_id>.+)$")
_STORAGE_CONTEXT_ID_RE = re.compile(r"^(?P<mode>[0-9A-Za-z]+)_(?P<stamp>\d{14,20})_(?P<context_id>.+)$")


def _gateway_override(name: str, default: Any) -> Any:
    try:
        from .. import gateway_server
    except Exception:
        return default
    return getattr(gateway_server, name, default)


def _utc_now_iso() -> str:
    """返回 UTC 时间 ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _chat_session_root_dir() -> Path:
    """返回 chat session 根目录。"""
    configured = (os.environ.get("MOBICLAW_CHAT_SESSION_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / ".mobiclaw" / "session"


def _chat_upload_root_dir() -> Path:
    """返回 chat 上传文件目录。"""
    return Path(__file__).resolve().parents[2] / ".mobiclaw" / "uploads"


def _sanitize_upload_name(name: str) -> str:
    """清洗上传文件名，避免路径穿透。"""
    candidate = Path(str(name or "").strip()).name
    return candidate or f"file_{uuid.uuid4().hex}"


def _normalize_input_files(input_files: list[str] | None) -> list[str]:
    """清洗并去重 input_files。"""
    normalized: list[str] = []
    seen: set[str] = set()
    for item in input_files or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _inject_input_files_into_task(task: str, input_files: list[str] | None) -> tuple[str, list[str]]:
    """将上传文件路径附加到任务文本，提示 Agent 读取分析。"""
    normalized = _normalize_input_files(input_files)
    if not normalized:
        return str(task or ""), []
    file_lines = "\n".join(f"- {path}" for path in normalized)
    injected = (
        f"{str(task or '').rstrip()}\n\n"
        "附加输入文件（本地路径）如下，请优先读取并分析这些文件内容：\n"
        f"{file_lines}"
    ).strip()
    return injected, normalized


def _normalize_context_id(raw: str | None) -> str:
    """规范化 context_id，避免非法路径字符。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"[^0-9A-Za-z._-]+", "-", text)
    return text.strip("-")


def _build_storage_context_id(context_id: str) -> str:
    normalized = _normalize_context_id(context_id)
    if not normalized:
        return ""
    if _STORAGE_CONTEXT_ID_RE.match(normalized):
        return normalized
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]
    return f"chat_{stamp}_{normalized}"


def _extract_context_alias(storage_context_id: str) -> str:
    normalized = _normalize_context_id(storage_context_id)
    if not normalized:
        return ""
    matched = _STORAGE_CONTEXT_ID_RE.match(normalized)
    if not matched:
        return normalized
    return _normalize_context_id(matched.group("context_id"))


def _parse_chat_session_dir_name(name: str) -> tuple[str, str] | None:
    """从目录名解析时间前缀与 context_id。"""
    candidate = str(name or "").strip()
    if not candidate:
        return None

    matched = _CHAT_SESSION_NAME_RE.match(candidate)
    if not matched:
        return None
    prefix = str(matched.group("prefix") or "").strip()
    storage_context_id = _normalize_context_id(matched.group("storage_context_id"))
    if not prefix or not storage_context_id:
        return None
    if not _STORAGE_CONTEXT_ID_RE.match(storage_context_id):
        return None
    return prefix, storage_context_id


def _scan_chat_session_dirs() -> list[dict[str, Any]]:
    """扫描 session 根目录，按目录名解析会话并按更新时间倒序返回。"""
    root = _gateway_override("_chat_session_root_dir", _chat_session_root_dir)()
    if not root.exists() or not root.is_dir():
        return []

    records: list[dict[str, Any]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        parsed = _parse_chat_session_dir_name(child.name)
        if parsed is None:
            continue
        _, context_id = parsed
        try:
            stat = child.stat()
        except OSError:
            continue
        updated_ts = float(stat.st_mtime)
        updated_at = datetime.fromtimestamp(updated_ts, tz=timezone.utc).isoformat()
        records.append(
            {
                "context_id": context_id,
                "session_id": context_id,
                "context_alias": _extract_context_alias(context_id),
                "dir_name": child.name,
                "path": str(child.resolve()),
                "updated_ts": updated_ts,
                "updated_at": updated_at,
            }
        )

    records.sort(key=lambda item: float(item.get("updated_ts", 0.0)), reverse=True)
    return records


def _latest_session_dir_for_context(context_id: str) -> Path | None:
    """返回指定 context_id 最新会话目录。"""
    normalized = _normalize_context_id(context_id)
    if not normalized:
        return None
    candidates = [
        item
        for item in _scan_chat_session_dirs()
        if item.get("context_id") == normalized or item.get("context_alias") == normalized
    ]
    if not candidates:
        return None
    path = str(candidates[0].get("path") or "").strip()
    if not path:
        return None
    target = Path(path)
    if not target.exists() or not target.is_dir():
        return None
    return target


def _ensure_session_dir_for_context(context_id: str) -> Path:
    """确保 context_id 对应目录存在；不存在则按约定命名创建。"""
    normalized = _normalize_context_id(context_id)
    if not normalized:
        raise ValueError("context_id is empty")

    existing = _latest_session_dir_for_context(normalized)
    if existing is not None:
        return existing

    root = _gateway_override("_chat_session_root_dir", _chat_session_root_dir)()
    root.mkdir(parents=True, exist_ok=True)
    storage_context_id = _build_storage_context_id(normalized)
    if not storage_context_id:
        raise ValueError("context_id is empty")
    dir_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}-{storage_context_id}"
    session_dir = root / dir_name
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _resolve_context_id(explicit_context_id: str | None, result: dict[str, Any] | None = None) -> str | None:
    """从显式参数或结果对象中解析 context_id。"""
    explicit = _normalize_context_id(explicit_context_id)
    if explicit:
        return explicit
    if not isinstance(result, dict):
        return None
    for key in ("context_id", "session_id"):
        value = _normalize_context_id(result.get(key))
        if value:
            return value
    session_obj = result.get("session")
    if isinstance(session_obj, dict):
        for key in ("context_id", "session_id"):
            value = _normalize_context_id(session_obj.get(key))
            if value:
                return value
    return None


def _append_history_line(history_path: Path, record: dict[str, Any]) -> None:
    """追加写入单条 JSONL 记录。"""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _append_chat_history(
    *,
    context_id: str | None,
    mode: str,
    job_id: str,
    user_text: str,
    assistant_text: str,
    status: str,
) -> None:
    """按约定写入 session 目录下 history.jsonl。"""
    resolved = _normalize_context_id(context_id)
    if not resolved:
        return
    try:
        session_dir = _ensure_session_dir_for_context(resolved)
    except Exception:
        logger.exception("Failed to ensure session directory for context_id=%s", resolved)
        return

    history_file = session_dir / "history.jsonl"
    ts = _utc_now_iso()
    shared_meta = {
        "job_id": job_id,
        "mode": str(mode or ""),
        "status": str(status or ""),
        "context_id": resolved,
    }

    user_message = str(user_text or "").strip()
    if user_message:
        _append_history_line(
            history_file,
            {
                "ts": ts,
                "role": "user",
                "name": "user",
                "text": user_message,
                "meta": shared_meta,
            },
        )

    assistant_message = str(assistant_text or "").strip()
    if assistant_message:
        _append_history_line(
            history_file,
            {
                "ts": ts,
                "role": "assistant",
                "name": "assistant",
                "text": assistant_message,
                "meta": shared_meta,
            },
        )


def _read_recent_session_messages(session_dir: Path, limit: int) -> list[dict[str, Any]]:
    """读取会话目录中 history.jsonl 最近 N 条消息。"""
    history_file = session_dir / "history.jsonl"
    if not history_file.exists() or not history_file.is_file():
        return []

    max_items = max(1, int(limit or 20))
    items: deque[dict[str, Any]] = deque(maxlen=max_items)
    try:
        with history_file.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                text = str(parsed.get("text") or "").strip()
                if not text:
                    continue
                role = str(parsed.get("role") or "assistant").strip().lower() or "assistant"
                if role not in {"user", "assistant", "system", "error"}:
                    role = "assistant"
                meta = parsed.get("meta")
                items.append(
                    {
                        "ts": str(parsed.get("ts") or ""),
                        "role": role,
                        "name": str(parsed.get("name") or role),
                        "text": text,
                        "meta": meta if isinstance(meta, dict) else {},
                    }
                )
    except OSError:
        logger.exception("Failed to read history.jsonl from session dir: %s", session_dir)
        return []
    return list(items)
