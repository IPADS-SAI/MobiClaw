from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
from agentscope.message import Msg

from seneschal import gateway_server
from seneschal import workflows
from seneschal.agents import ChatSessionManager


class _DummyChatAgent:
    async def __call__(self, msg: Msg) -> Msg:
        return Msg("ChatAssistant", f"ECHO::{msg.get_text_content()}", "assistant")

    async def handle_interrupt(self, *args, **kwargs) -> Msg:  # noqa: ANN002, ANN003
        return Msg("ChatAssistant", "dummy interrupted", "assistant")

    async def interrupt(self, msg: Msg | list[Msg] | None = None) -> None:
        del msg

    def state_dict(self) -> dict:
        return {}

    def load_state_dict(self, state_dict: dict) -> None:
        del state_dict


def _make_session_dir(root: Path, dir_name: str, mtime: int, history_rows: list[dict] | None = None) -> Path:
    target = root / dir_name
    target.mkdir(parents=True, exist_ok=True)
    os.utime(target, (mtime, mtime))
    if history_rows is not None:
        history_file = target / "history.jsonl"
        with history_file.open("w", encoding="utf-8") as handle:
            for row in history_rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return target


def test_list_chat_sessions_sorted_desc(monkeypatch, tmp_path: Path):
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    _make_session_dir(root, "20260312_120000_000001-chat-s1", mtime=10)
    _make_session_dir(root, "20260312_120000_000002-chat-s2", mtime=30)
    _make_session_dir(root, "20260312_120000_000003-chat-s3", mtime=20)

    monkeypatch.setenv("SENESCHAL_GATEWAY_API_KEY", "")
    monkeypatch.setattr(gateway_server, "_chat_session_root_dir", lambda: root)

    with TestClient(gateway_server.app) as client:
        resp = client.get("/api/v1/chat/sessions")
        assert resp.status_code == 200
        payload = resp.json()
        sessions = payload.get("sessions", [])
        assert [item["context_id"] for item in sessions] == ["s2", "s3", "s1"]


def test_get_chat_session_recent_messages_limit(monkeypatch, tmp_path: Path):
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for idx in range(30):
        rows.append(
            {
                "ts": f"2026-03-12T10:00:{idx:02d}Z",
                "role": "user" if idx % 2 == 0 else "assistant",
                "name": "user" if idx % 2 == 0 else "assistant",
                "text": f"line-{idx}",
                "meta": {"idx": idx},
            }
        )
    _make_session_dir(root, "20260312_120000_000010-chat-limit_ctx", mtime=50, history_rows=rows)

    monkeypatch.setenv("SENESCHAL_GATEWAY_API_KEY", "")
    monkeypatch.setattr(gateway_server, "_chat_session_root_dir", lambda: root)

    with TestClient(gateway_server.app) as client:
        resp = client.get("/api/v1/chat/sessions/limit_ctx?limit=20")
        assert resp.status_code == 200
        payload = resp.json()
        messages = payload.get("messages", [])
        assert len(messages) == 20
        assert messages[0]["text"] == "line-10"
        assert messages[-1]["text"] == "line-29"


def test_get_chat_session_without_history_file(monkeypatch, tmp_path: Path):
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    _make_session_dir(root, "20260312_120000_000100-chat-empty_ctx", mtime=70, history_rows=None)

    monkeypatch.setenv("SENESCHAL_GATEWAY_API_KEY", "")
    monkeypatch.setattr(gateway_server, "_chat_session_root_dir", lambda: root)

    with TestClient(gateway_server.app) as client:
        resp = client.get("/api/v1/chat/sessions/empty_ctx?limit=20")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload.get("messages") == []


def test_chat_mode_not_double_append_history(monkeypatch, tmp_path: Path):
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)

    manager = ChatSessionManager(root_dir=root)
    monkeypatch.setattr(workflows, "_CHAT_SESSION_MANAGER", manager)
    monkeypatch.setattr(workflows, "create_chat_agent", lambda: _DummyChatAgent())
    monkeypatch.setattr(gateway_server, "_chat_session_root_dir", lambda: root)
    monkeypatch.setenv("SENESCHAL_GATEWAY_API_KEY", "")

    with TestClient(gateway_server.app) as client:
        resp = client.post(
            "/api/v1/task",
            json={
                "task": "你好",
                "mode": "chat",
                "context_id": "dup_ctx",
                "async_mode": False,
            },
        )
        assert resp.status_code == 200

    sessions = [p for p in root.iterdir() if p.is_dir() and p.name.endswith("-chat-dup_ctx")]
    assert sessions, "expected chat session directory to be created"
    history_file = sessions[0] / "history.jsonl"
    assert history_file.exists()
    lines = [line for line in history_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    # first round should contain:
    # 1) auto intro assistant message
    # 2) user message
    # 3) assistant reply
    assert len(lines) == 3
