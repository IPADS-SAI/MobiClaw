from __future__ import annotations

import asyncio
from pathlib import Path

from agentscope.message import Msg

from seneschal import workflows
from seneschal.agents import ChatSessionManager


class _DummyChatAgent:
    def __init__(self, delay_s: float = 0.0) -> None:
        self.delay_s = delay_s
        self._running_task: asyncio.Task | None = None

    async def __call__(self, msg: Msg) -> Msg:
        self._running_task = asyncio.current_task()
        try:
            if self.delay_s > 0:
                await asyncio.sleep(self.delay_s)
        except asyncio.CancelledError:
            return await self.handle_interrupt()
        finally:
            self._running_task = None
        return Msg("ChatAssistant", f"ECHO::{msg.get_text_content()}", "assistant")

    async def handle_interrupt(self, *args, **kwargs) -> Msg:  # noqa: ANN002, ANN003
        return Msg("ChatAssistant", "dummy interrupted", "assistant")

    async def interrupt(self, msg: Msg | list[Msg] | None = None) -> None:
        del msg
        if self._running_task and not self._running_task.done():
            self._running_task.cancel()

    def state_dict(self) -> dict:
        return {}

    def load_state_dict(self, state_dict: dict) -> None:
        del state_dict


def test_chat_session_priority_and_new(monkeypatch, tmp_path: Path):
    manager = ChatSessionManager(root_dir=tmp_path / "session")
    monkeypatch.setattr(workflows, "_CHAT_SESSION_MANAGER", manager)
    monkeypatch.setattr(workflows, "create_chat_agent", lambda: _DummyChatAgent())

    first = asyncio.run(
        workflows.run_gateway_task(
            task="你好",
            mode="chat",
            context_id="my_ctx",
        ),
    )
    assert first["session"]["session_id"] == "my_ctx"

    second = asyncio.run(
        workflows.run_gateway_task(
            task="继续聊",
            mode="chat",
            context_id=None,
        ),
    )
    assert second["session"]["session_id"] == "my_ctx"
    assert second["session"]["resumed_from_latest"] is True

    third = asyncio.run(
        workflows.run_gateway_task(
            task="/new",
            mode="chat",
            context_id=None,
        ),
    )
    assert third["session"]["session_id"] != "my_ctx"
    assert third["session"]["is_new_session"] is True
    assert third["session"]["command"] == "new"


def test_chat_first_turn_introduces_once(monkeypatch, tmp_path: Path):
    manager = ChatSessionManager(root_dir=tmp_path / "session")
    monkeypatch.setattr(workflows, "_CHAT_SESSION_MANAGER", manager)
    monkeypatch.setattr(workflows, "create_chat_agent", lambda: _DummyChatAgent())

    first = asyncio.run(
        workflows.run_gateway_task(
            task="你是谁",
            mode="chat",
            context_id="intro_ctx",
        ),
    )
    assert "Seneschal 的 chat 助手" in first["reply"]

    second = asyncio.run(
        workflows.run_gateway_task(
            task="你还能做什么",
            mode="chat",
            context_id="intro_ctx",
        ),
    )
    assert "Seneschal 的 chat 助手" not in second["reply"]


def test_chat_interrupt_without_active_reply(monkeypatch, tmp_path: Path):
    manager = ChatSessionManager(root_dir=tmp_path / "session")
    monkeypatch.setattr(workflows, "_CHAT_SESSION_MANAGER", manager)
    monkeypatch.setattr(workflows, "create_chat_agent", lambda: _DummyChatAgent())

    result = asyncio.run(
        workflows.run_gateway_task(
            task="/interrupt",
            mode="chat",
            context_id="interrupt_ctx",
        ),
    )
    assert "dummy interrupted" in result["reply"]
    assert result["session"]["command"] == "interrupt"


def test_chat_interrupt_with_active_reply(monkeypatch, tmp_path: Path):
    manager = ChatSessionManager(root_dir=tmp_path / "session")
    monkeypatch.setattr(workflows, "_CHAT_SESSION_MANAGER", manager)

    calls = {"n": 0}

    def _factory() -> _DummyChatAgent:
        calls["n"] += 1
        if calls["n"] == 1:
            return _DummyChatAgent(delay_s=0.4)
        return _DummyChatAgent(delay_s=0.0)

    monkeypatch.setattr(workflows, "create_chat_agent", _factory)

    async def _run() -> tuple[dict, dict]:
        long_task = asyncio.create_task(
            workflows.run_gateway_task(
                task="请慢慢回答",
                mode="chat",
                context_id="active_ctx",
            ),
        )
        await asyncio.sleep(0.08)
        interrupt_result = await workflows.run_gateway_task(
            task="/interrupt",
            mode="chat",
            context_id="active_ctx",
        )
        long_result = await long_task
        return long_result, interrupt_result

    long_result, interrupt_result = asyncio.run(_run())
    assert interrupt_result["reply"].startswith("已收到中断指令")
    assert "dummy interrupted" in long_result["reply"]
