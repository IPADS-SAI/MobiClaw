from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from agentscope.message import Msg

from mobiclaw import workflows
from mobiclaw.session import ChatSessionManager


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


@dataclass
class _DummySubTask:
    id: str
    name: str
    description: str
    expected_outcome: str
    state: str = "todo"
    outcome: str = ""
    created_at: str = ""
    finished_at: str = ""


@dataclass
class _DummyPlan:
    id: str
    name: str
    description: str
    expected_outcome: str
    state: str = "todo"
    outcome: str = ""
    created_at: str = ""
    finished_at: str = ""
    subtasks: list[_DummySubTask] = field(default_factory=list)


class _DummyPlanNotebook:
    def __init__(self) -> None:
        self.current_plan: _DummyPlan | None = None
        self._hooks: dict[str, object] = {}

    def register_plan_change_hook(self, hook_name: str, hook: object) -> None:
        self._hooks[hook_name] = hook

    def remove_plan_change_hook(self, hook_name: str) -> None:
        self._hooks.pop(hook_name, None)

    async def trigger(self) -> None:
        for hook in list(self._hooks.values()):
            result = hook(self, self.current_plan)
            if asyncio.iscoroutine(result):
                await result


class _DummyPlannerAgent(_DummyChatAgent):
    def __init__(self) -> None:
        super().__init__(delay_s=0.0)
        self.plan_notebook = _DummyPlanNotebook()

    async def __call__(self, msg: Msg) -> Msg:
        del msg
        plan = _DummyPlan(
            id="plan-1",
            name="测试计划",
            description="测试描述",
            expected_outcome="测试期望",
            state="todo",
            subtasks=[
                _DummySubTask(
                    id="st-1",
                    name="检索信息",
                    description="检索相关信息",
                    expected_outcome="获取检索结果",
                    state="todo",
                ),
                _DummySubTask(
                    id="st-2",
                    name="生成总结",
                    description="整合并总结",
                    expected_outcome="输出最终总结",
                    state="todo",
                ),
            ],
        )
        self.plan_notebook.current_plan = plan
        await self.plan_notebook.trigger()  # plan_created

        plan.state = "in_progress"
        plan.subtasks[0].state = "in_progress"
        await self.plan_notebook.trigger()  # subtask_activated

        plan.subtasks[0].state = "done"
        plan.subtasks[0].outcome = "拿到检索结果"
        await self.plan_notebook.trigger()  # subtask_done

        plan.subtasks[1].state = "in_progress"
        await self.plan_notebook.trigger()  # subtask_activated

        plan.subtasks[1].state = "done"
        plan.subtasks[1].outcome = "生成最终总结"
        plan.state = "done"
        plan.outcome = "最终总结已完成"
        await self.plan_notebook.trigger()  # plan_done

        self.plan_notebook.current_plan = None
        await self.plan_notebook.trigger()  # plan_done/closed

        return Msg("ChatAssistant", "", "assistant")


def test_chat_session_priority_and_new(monkeypatch, tmp_path: Path):
    manager = ChatSessionManager(root_dir=tmp_path / "session")
    monkeypatch.setattr(workflows, "_CHAT_SESSION_MANAGER", manager)
    monkeypatch.setattr(workflows, "create_chat_agent", lambda **_: _DummyChatAgent())

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
    monkeypatch.setattr(workflows, "create_chat_agent", lambda **_: _DummyChatAgent())

    first = asyncio.run(
        workflows.run_gateway_task(
            task="你是谁",
            mode="chat",
            context_id="intro_ctx",
        ),
    )
    assert "我是 MobiClaw 助手 MobiChatBot" in first["reply"]

    second = asyncio.run(
        workflows.run_gateway_task(
            task="你还能做什么",
            mode="chat",
            context_id="intro_ctx",
        ),
    )
    assert "我是 MobiClaw 助手 MobiChatBot" not in second["reply"]


def test_chat_interrupt_without_active_reply(monkeypatch, tmp_path: Path):
    manager = ChatSessionManager(root_dir=tmp_path / "session")
    monkeypatch.setattr(workflows, "_CHAT_SESSION_MANAGER", manager)
    monkeypatch.setattr(workflows, "create_chat_agent", lambda **_: _DummyChatAgent())

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

    def _factory(**_: object) -> _DummyChatAgent:
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


def test_chat_reply_fallback_from_planner(monkeypatch, tmp_path: Path):
    manager = ChatSessionManager(root_dir=tmp_path / "session")
    monkeypatch.setattr(workflows, "_CHAT_SESSION_MANAGER", manager)
    monkeypatch.setattr(workflows, "create_chat_agent", lambda **_: _DummyPlannerAgent())

    result = asyncio.run(
        workflows.run_gateway_task(
            task="请执行计划任务",
            mode="chat",
            context_id="planner_ctx",
        ),
    )
    assert result["reply"]
    assert "测试计划" in result["reply"]
    assert "最终总结已完成" in result["reply"]
    assert result.get("reply_fallback")


def test_chat_planner_events_have_delta_and_event_key(monkeypatch, tmp_path: Path):
    manager = ChatSessionManager(root_dir=tmp_path / "session")
    monkeypatch.setattr(workflows, "_CHAT_SESSION_MANAGER", manager)
    monkeypatch.setattr(workflows, "create_chat_agent", lambda **_: _DummyPlannerAgent())

    progress_events: list[dict] = []

    async def _progress_callback(payload: dict) -> None:
        progress_events.append(payload)

    result = asyncio.run(
        workflows.run_gateway_task(
            task="请执行计划任务",
            mode="chat",
            context_id="planner_ctx_events",
            progress_callback=_progress_callback,
        ),
    )
    planner = result["planner_monitor"]
    events = planner["events"]
    assert len(events) >= 4
    assert all("event_key" in e and e["event_key"] for e in events)
    assert all("delta" in e and isinstance(e["delta"], dict) for e in events)
    event_types = [str(e.get("event_type") or "") for e in events]
    assert "plan_created" in event_types
    assert "subtask_done" in event_types
    assert any(t in {"plan_done", "plan_abandoned"} for t in event_types)
    assert any(p.get("channel") == "planner_monitor" for p in progress_events)
