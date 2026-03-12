# -*- coding: utf-8 -*-
"""Seneschal 会话管理模块。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import random
import re
import string
from typing import Any

from agentscope.agent import ReActAgent
from agentscope.session import JSONSession

logger = logging.getLogger(__name__)


@dataclass
class GenericSessionHandle:
    """通用会话句柄。"""

    session_id: str
    session_dir: Path
    is_new_session: bool
    resumed_from_latest: bool
    meta: dict[str, Any]


class GenericSessionManager:
    """管理任意模式会话状态、历史与中断。"""

    _SESSION_DIR_RE = re.compile(r"^(?P<prefix>\d{8}_\d{6}_\d{6})-(?P<storage_session_id>.+)$")
    _STORAGE_SESSION_ID_RE = re.compile(r"^(?P<mode>[0-9A-Za-z]+)_(?P<stamp>\d{14,20})_(?P<session_id>.+)$")

    def __init__(self, root_dir: str | Path | None = None) -> None:
        configured = str(root_dir or os.environ.get("SENESCHAL_SESSION_ROOT", "")).strip()
        if configured:
            self.root_dir = Path(configured).expanduser()
        else:
            self.root_dir = Path(__file__).resolve().parents[2] / ".mobiclaw" / "session"
        self.latest_pointer = self.root_dir / "latest_session.json"
        self._active_replies: dict[str, tuple[ReActAgent, asyncio.Task[Any]]] = {}
        self._active_lock = asyncio.Lock()

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_session_id(raw: str | None) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        text = re.sub(r"[^0-9A-Za-z._-]+", "-", text)
        return text.strip("-")

    @staticmethod
    def _generate_session_id() -> str:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"chat_{stamp}_{suffix}"

    @staticmethod
    def _normalize_mode(raw: str | None) -> str:
        text = str(raw or "").strip().lower()
        if not text:
            return "chat"
        text = re.sub(r"[^0-9A-Za-z]+", "", text)
        return text or "chat"

    @staticmethod
    def _normalize_agent_key(raw: str | None) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        text = re.sub(r"[^0-9A-Za-z._-]+", "-", text)
        return text.strip("-")

    def _ensure_root(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _session_meta_path(self, session_dir: Path) -> Path:
        return session_dir / "meta.json"

    def _history_path(self, session_dir: Path) -> Path:
        return session_dir / "history.jsonl"

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists() or not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_history(self, session_dir: Path, record: dict[str, Any]) -> None:
        path = self._history_path(session_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _compact_stamp(stamp: str) -> str:
        return re.sub(r"[^0-9]+", "", str(stamp or "").strip())

    def _parse_session_dir_name(self, name: str) -> tuple[str, str, str, str] | None:
        candidate = str(name or "").strip()
        if not candidate:
            return None

        matched = self._SESSION_DIR_RE.match(candidate)
        if not matched:
            return None
        prefix = str(matched.group("prefix") or "").strip()
        storage_session_id = self._normalize_session_id(matched.group("storage_session_id"))
        if not prefix or not storage_session_id:
            return None
        storage_parts = self._STORAGE_SESSION_ID_RE.match(storage_session_id)
        if not storage_parts:
            return None
        mode = self._normalize_mode(storage_parts.group("mode"))
        session_id = self._normalize_session_id(storage_parts.group("session_id"))
        if not session_id:
            return None
        return prefix, storage_session_id, session_id, mode

    def _list_session_dirs(self) -> list[dict[str, Any]]:
        self._ensure_root()
        records: list[dict[str, Any]] = []
        for child in self.root_dir.iterdir():
            if not child.is_dir():
                continue
            parsed = self._parse_session_dir_name(child.name)
            if parsed is None:
                continue
            _, storage_session_id, session_id, mode = parsed
            try:
                stat = child.stat()
            except OSError:
                continue
            records.append(
                {
                    "session_id": session_id,
                    "storage_session_id": storage_session_id,
                    "mode": mode,
                    "path": child,
                    "updated_ts": float(stat.st_mtime),
                }
            )
        records.sort(key=lambda item: float(item.get("updated_ts", 0.0)), reverse=True)
        return records

    def _find_latest_dir_for_session(self, session_id: str) -> Path | None:
        normalized = self._normalize_session_id(session_id)
        if not normalized:
            return None
        for item in self._list_session_dirs():
            if item.get("session_id") == normalized or item.get("storage_session_id") == normalized:
                target = item.get("path")
                if isinstance(target, Path):
                    return target
        return None

    def _create_session_dir(self, session_id: str, *, mode: str = "chat") -> Path:
        storage_session_id = self._build_storage_session_id(session_id, mode=mode)
        if not storage_session_id:
            raise ValueError("session_id is empty")
        self._ensure_root()
        dir_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}-{storage_session_id}"
        session_dir = self.root_dir / dir_name
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def _build_storage_session_id(self, session_id: str, *, mode: str = "chat") -> str:
        normalized = self._normalize_session_id(session_id)
        if self._STORAGE_SESSION_ID_RE.match(normalized):
            return normalized
        if not normalized:
            return ""
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]
        return f"{self._normalize_mode(mode)}_{stamp}_{normalized}"

    def _build_agent_state_key(
        self,
        *,
        storage_session_id: str,
        agent_key: str | None,
    ) -> str:
        normalized_agent = self._normalize_agent_key(agent_key)
        if not normalized_agent:
            return storage_session_id
        return f"{storage_session_id}__{normalized_agent}"

    @staticmethod
    def _session_state_path(session_dir: Path, session_id: str) -> Path:
        return session_dir / f"{session_id}.json"

    def _read_latest_pointer(self) -> dict[str, Any]:
        return self._read_json(self.latest_pointer)

    def _write_latest_pointer(self, *, session_id: str, session_dir: Path) -> None:
        parsed = self._parse_session_dir_name(session_dir.name)
        self._write_json(
            self.latest_pointer,
            {
                "session_id": session_id,
                "storage_session_id": parsed[1] if parsed is not None else self._build_storage_session_id(session_id),
                "mode": parsed[3] if parsed is not None else "chat",
                "session_dir": str(session_dir.resolve()),
                "updated_at": self._utc_now_iso(),
            },
        )

    def _extract_mode_storage_ids(
        self,
        *,
        handle: GenericSessionHandle,
    ) -> dict[str, str]:
        mode_map: dict[str, str] = {}
        raw_map = handle.meta.get("storage_session_ids")
        if isinstance(raw_map, dict):
            for raw_mode, raw_id in raw_map.items():
                mode = self._normalize_mode(raw_mode)
                sid = self._normalize_session_id(raw_id)
                if mode and sid:
                    mode_map[mode] = sid
        legacy_sid = self._normalize_session_id(handle.meta.get("storage_session_id"))
        if legacy_sid:
            parsed = self._STORAGE_SESSION_ID_RE.match(legacy_sid)
            if parsed:
                mode_map.setdefault(self._normalize_mode(parsed.group("mode")), legacy_sid)
            else:
                mode_map.setdefault("chat", legacy_sid)
        parsed_dir = self._parse_session_dir_name(handle.session_dir.name)
        if parsed_dir is not None:
            mode_map.setdefault(parsed_dir[3], parsed_dir[1])
        return mode_map

    def _resolve_storage_session_id(
        self,
        *,
        handle: GenericSessionHandle,
        mode: str,
        create_if_missing: bool,
    ) -> str:
        mode_map = self._extract_mode_storage_ids(handle=handle)
        resolved_mode = self._normalize_mode(mode)
        existing = self._normalize_session_id(mode_map.get(resolved_mode))
        if existing:
            return existing
        if not create_if_missing:
            return ""
        created = self._build_storage_session_id(handle.session_id, mode=resolved_mode)
        if created:
            mode_map[resolved_mode] = created
            handle.meta["storage_session_ids"] = mode_map
        return created

    async def resolve_session(
        self,
        context_id: str | None,
        *,
        force_new: bool = False,
        mode: str = "chat",
    ) -> GenericSessionHandle:
        """根据优先级选择或创建会话。"""
        normalized_mode = self._normalize_mode(mode)
        normalized_context = self._normalize_session_id(context_id)
        resumed_from_latest = False
        is_new_session = False

        if force_new:
            session_id = normalized_context or self._generate_session_id()
            session_dir = self._create_session_dir(session_id, mode=normalized_mode)
            is_new_session = True
        elif normalized_context:
            session_id = normalized_context
            existing = self._find_latest_dir_for_session(session_id)
            if existing is not None:
                session_dir = existing
                is_new_session = False
            else:
                session_dir = self._create_session_dir(session_id, mode=normalized_mode)
                is_new_session = True
        else:
            latest = self._read_latest_pointer()
            latest_id = self._normalize_session_id(latest.get("session_id"))
            latest_path = Path(str(latest.get("session_dir") or "")).expanduser() if latest.get("session_dir") else None
            if latest_id and latest_path and latest_path.exists() and latest_path.is_dir():
                session_id = latest_id
                session_dir = latest_path
                resumed_from_latest = True
            else:
                session_id = self._generate_session_id()
                session_dir = self._create_session_dir(session_id, mode=normalized_mode)
                is_new_session = True

        meta = self._read_json(self._session_meta_path(session_dir))
        parsed = self._parse_session_dir_name(session_dir.name)
        if parsed is not None:
            storage_session_ids = meta.get("storage_session_ids")
            if not isinstance(storage_session_ids, dict):
                storage_session_ids = {}
            storage_session_ids.setdefault(parsed[3], parsed[1])
            meta["storage_session_ids"] = storage_session_ids
            meta.setdefault("storage_session_id", parsed[1])
        meta.setdefault("session_id", session_id)
        meta.setdefault("mode", normalized_mode)
        self._write_latest_pointer(session_id=session_id, session_dir=session_dir)
        return GenericSessionHandle(
            session_id=session_id,
            session_dir=session_dir,
            is_new_session=is_new_session,
            resumed_from_latest=resumed_from_latest,
            meta=meta,
        )

    async def load_agent_state(
        self,
        handle: GenericSessionHandle,
        agent: ReActAgent,
        *,
        mode: str = "chat",
        agent_key: str | None = None,
    ) -> None:
        """加载会话中的 agent 状态。"""
        resolved_mode = self._normalize_mode(mode)
        storage_session_id = self._resolve_storage_session_id(
            handle=handle,
            mode=resolved_mode,
            create_if_missing=False,
        )
        if not storage_session_id:
            logger.info("Skip loading state: empty storage_session_id mode=%s", resolved_mode)
            return

        normalized_agent_key = self._normalize_agent_key(agent_key)
        mode_state_keys = handle.meta.get("agent_state_keys")
        state_key = ""
        if isinstance(mode_state_keys, dict):
            mode_entries = mode_state_keys.get(resolved_mode)
            if isinstance(mode_entries, dict) and normalized_agent_key:
                state_key = self._normalize_session_id(mode_entries.get(normalized_agent_key))
        if not state_key:
            state_key = self._build_agent_state_key(
                storage_session_id=storage_session_id,
                agent_key=normalized_agent_key if normalized_agent_key else None,
            )
        if (
            normalized_agent_key
            and resolved_mode == "chat"
            and state_key != storage_session_id
            and not self._session_state_path(handle.session_dir, state_key).exists()
        ):
            # Backward compatibility: old chat sessions used one file per session.
            state_key = storage_session_id
        state_path = self._session_state_path(handle.session_dir, state_key)
        if not state_path.exists() or not state_path.is_file():
            logger.info("Session state file not found: %s", state_path)
            return
        data = self._read_json(state_path)
        agent_state = data.get("agent") if isinstance(data, dict) else None
        if not isinstance(agent_state, dict):
            logger.warning("Invalid agent state file: %s", state_path)
            return
        try:
            agent.load_state_dict(agent_state, strict=False)
            logger.info("Loaded agent state from %s mode=%s agent=%s", state_path, resolved_mode, normalized_agent_key)
        except Exception:
            logger.warning("Failed to load agent state from %s", state_path, exc_info=True)

    async def save_agent_state(
        self,
        handle: GenericSessionHandle,
        agent: ReActAgent,
        *,
        command: str,
        introduced: bool | None = None,
        mode: str = "chat",
        agent_key: str | None = None,
    ) -> None:
        """保存会话中的 agent 状态与元数据。"""
        resolved_mode = self._normalize_mode(mode)
        session = JSONSession(save_dir=str(handle.session_dir))
        storage_session_id = self._resolve_storage_session_id(
            handle=handle,
            mode=resolved_mode,
            create_if_missing=True,
        )
        normalized_agent_key = self._normalize_agent_key(agent_key)
        current_meta = self._read_json(self._session_meta_path(handle.session_dir))
        if isinstance(handle.meta, dict):
            current_meta.update(handle.meta)
        agent_state_keys = current_meta.get("agent_state_keys")
        if not isinstance(agent_state_keys, dict):
            agent_state_keys = {}
        mode_keys = agent_state_keys.get(resolved_mode)
        if not isinstance(mode_keys, dict):
            mode_keys = {}
        state_key = ""
        if normalized_agent_key:
            state_key = self._normalize_session_id(mode_keys.get(normalized_agent_key))
        if not state_key:
            state_key = self._build_agent_state_key(
                storage_session_id=storage_session_id,
                agent_key=normalized_agent_key if normalized_agent_key else None,
            )
        if normalized_agent_key:
            mode_keys[normalized_agent_key] = state_key
            agent_state_keys[resolved_mode] = mode_keys
            current_meta["agent_state_keys"] = agent_state_keys
        await session.save_session_state(
            session_id=state_key,
            agent=agent,
        )
        storage_session_ids = current_meta.get("storage_session_ids")
        if not isinstance(storage_session_ids, dict):
            storage_session_ids = {}
        storage_session_ids[resolved_mode] = storage_session_id
        current_meta["storage_session_ids"] = storage_session_ids
        current_meta.update(
            {
                "session_id": handle.session_id,
                "storage_session_id": storage_session_id,
                "session_dir": str(handle.session_dir.resolve()),
                "updated_at": self._utc_now_iso(),
                "last_command": command,
                "mode": resolved_mode,
                "last_mode": resolved_mode,
            }
        )
        if introduced is not None:
            current_meta["introduced"] = bool(introduced)
        if "created_at" not in current_meta:
            current_meta["created_at"] = self._utc_now_iso()
        self._write_json(self._session_meta_path(handle.session_dir), current_meta)
        self._write_latest_pointer(session_id=handle.session_id, session_dir=handle.session_dir)
        handle.meta = current_meta

    def append_history_message(
        self,
        *,
        handle: GenericSessionHandle,
        role: str,
        text: str,
        name: str | None = None,
        mode: str = "chat",
        command: str = "",
        agent: str | None = None,
        extra_meta: dict[str, Any] | None = None,
    ) -> None:
        """记录单条历史消息到 history.jsonl。"""
        message = str(text or "").strip()
        if not message:
            return
        resolved_mode = self._normalize_mode(mode)
        normalized_agent = self._normalize_agent_key(agent)
        meta: dict[str, Any] = {
            "session_id": handle.session_id,
            "mode": resolved_mode,
            "command": str(command or ""),
        }
        if normalized_agent:
            meta["agent"] = normalized_agent
        if isinstance(extra_meta, dict):
            for key, value in extra_meta.items():
                if key not in meta:
                    meta[key] = value
        self._append_history(
            handle.session_dir,
            {
                "ts": self._utc_now_iso(),
                "role": str(role or "assistant").strip().lower() or "assistant",
                "name": str(name or role or "assistant").strip() or "assistant",
                "text": message,
                "meta": meta,
            },
        )

    def append_turn_history(
        self,
        *,
        handle: GenericSessionHandle,
        user_text: str,
        assistant_text: str,
        command: str,
        mode: str = "chat",
    ) -> None:
        """记录对话轮次到 history.jsonl。"""
        self.append_history_message(
            handle=handle,
            role="user",
            name="user",
            text=user_text,
            mode=mode,
            command=command,
        )
        self.append_history_message(
            handle=handle,
            role="assistant",
            name="assistant",
            text=assistant_text,
            mode=mode,
            command=command,
        )

    async def register_active_reply(
        self,
        session_id: str,
        agent: ReActAgent,
        task: asyncio.Task[Any],
    ) -> None:
        """注册当前会话活跃回复任务。"""
        async with self._active_lock:
            self._active_replies[session_id] = (agent, task)

    async def unregister_active_reply(
        self,
        session_id: str,
        task: asyncio.Task[Any],
    ) -> None:
        """注销当前会话活跃回复任务。"""
        async with self._active_lock:
            current = self._active_replies.get(session_id)
            if current and current[1] is task:
                self._active_replies.pop(session_id, None)

    async def interrupt_session(self, session_id: str) -> bool:
        """尝试中断某会话当前活跃回复。"""
        async with self._active_lock:
            current = self._active_replies.get(session_id)
        if not current:
            return False
        agent, _ = current
        await agent.interrupt()
        return True


ChatSessionHandle = GenericSessionHandle
ChatSessionManager = GenericSessionManager
