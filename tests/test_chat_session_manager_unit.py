from __future__ import annotations

import asyncio
import json
from pathlib import Path

from seneschal.agents import ChatSessionManager


class _DummyAgent:
    def __init__(self) -> None:
        self.loaded: list[tuple[dict, bool]] = []
        self.interrupted = False

    def load_state_dict(self, state: dict, strict: bool = True) -> None:
        self.loaded.append((state, strict))

    async def interrupt(self, msg=None) -> None:  # noqa: ANN001
        del msg
        self.interrupted = True


class _DummyJSONSession:
    calls: list[tuple[str, object]] = []

    def __init__(self, save_dir: str) -> None:
        self.save_dir = save_dir

    async def save_session_state(self, session_id: str, agent: object) -> None:
        self.__class__.calls.append((session_id, agent))


def test_parse_and_normalize_session_id() -> None:
    manager = ChatSessionManager(root_dir=Path("/tmp/chat-session-test"))

    assert manager._normalize_session_id(" abc 123 ") == "abc-123"
    assert manager._normalize_session_id("中文 id") == "id"
    assert manager._parse_session_dir_name("invalid") is None


def test_resolve_session_happy_path_and_latest_pointer(tmp_path: Path) -> None:
    manager = ChatSessionManager(root_dir=tmp_path / "session")

    first = asyncio.run(manager.resolve_session("ctx-1"))
    assert first.session_id == "ctx-1"
    assert first.is_new_session is True

    second = asyncio.run(manager.resolve_session("ctx-1"))
    assert second.session_id == "ctx-1"
    assert second.is_new_session is False

    latest = asyncio.run(manager.resolve_session(None))
    assert latest.session_id == "ctx-1"
    assert latest.resumed_from_latest is True


def test_load_agent_state_missing_or_invalid_file(tmp_path: Path) -> None:
    manager = ChatSessionManager(root_dir=tmp_path / "session")
    handle = asyncio.run(manager.resolve_session("ctx-load"))
    agent = _DummyAgent()

    asyncio.run(manager.load_agent_state(handle, agent))
    assert agent.loaded == []

    parsed = manager._parse_session_dir_name(handle.session_dir.name)
    assert parsed is not None
    storage_session_id = parsed[1]
    state_path = handle.session_dir / f"{storage_session_id}.json"
    state_path.write_text(json.dumps({"agent": "bad"}, ensure_ascii=False), encoding="utf-8")

    asyncio.run(manager.load_agent_state(handle, agent))
    assert agent.loaded == []


def test_load_agent_state_success(tmp_path: Path) -> None:
    manager = ChatSessionManager(root_dir=tmp_path / "session")
    handle = asyncio.run(manager.resolve_session("ctx-ok"))
    agent = _DummyAgent()

    parsed = manager._parse_session_dir_name(handle.session_dir.name)
    assert parsed is not None
    storage_session_id = parsed[1]
    state_path = handle.session_dir / f"{storage_session_id}.json"
    state_path.write_text(json.dumps({"agent": {"memory": {"k": "v"}}}, ensure_ascii=False), encoding="utf-8")

    asyncio.run(manager.load_agent_state(handle, agent))
    assert len(agent.loaded) == 1
    assert agent.loaded[0][0] == {"memory": {"k": "v"}}
    assert agent.loaded[0][1] is False


def test_save_agent_state_and_interrupt(monkeypatch, tmp_path: Path) -> None:
    manager = ChatSessionManager(root_dir=tmp_path / "session")
    handle = asyncio.run(manager.resolve_session("ctx-save"))
    agent = _DummyAgent()

    _DummyJSONSession.calls = []
    monkeypatch.setattr("seneschal.agents.JSONSession", _DummyJSONSession)

    asyncio.run(manager.save_agent_state(handle, agent, command="message", introduced=True))

    assert _DummyJSONSession.calls
    meta_path = handle.session_dir / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["session_id"] == "ctx-save"
    assert meta["last_command"] == "message"
    assert meta["introduced"] is True

    assert asyncio.run(manager.interrupt_session("not-exist")) is False

    task = asyncio.run(_create_dummy_task())
    asyncio.run(manager.register_active_reply("ctx-save", agent, task))
    assert asyncio.run(manager.interrupt_session("ctx-save")) is True
    assert agent.interrupted is True
    task.cancel()


async def _create_dummy_task():
    return asyncio.create_task(asyncio.sleep(5))
