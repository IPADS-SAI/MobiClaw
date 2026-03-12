# -*- coding: utf-8 -*-
"""Seneschal 工作流编排入口。

适用场景：
- CLI 演示模式与交互模式；
- 网关任务转发到编排层；
- 日常任务（daily）触发执行。

依赖模块：
- `seneschal.agents`：创建用户/执行 Agent；
- `seneschal.orchestrator`：多 Agent 智能路由与执行；
- `seneschal.dailytasks.runner`：定时/触发型每日任务流程。
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from agentscope.message import Msg

from .agents import (
    ChatSessionManager,
    create_chat_agent,
    create_steward_agent,
    create_user_agent,
    create_worker_agent,
)
from .dailytasks.runner import run_daily_tasks
from .orchestrator import run_orchestrated_task

logger = logging.getLogger(__name__)
_CHAT_SESSION_MANAGER = ChatSessionManager()
PlannerProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


async def _emit_progress(
    callback: PlannerProgressCallback | None,
    payload: dict[str, Any],
) -> None:
    """向调用方发送执行中进度（同步/异步回调均兼容）。"""
    if callback is None:
        return
    try:
        result = callback(payload)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.warning("Failed to emit planner progress update", exc_info=True)


def _serialize_plan_for_monitor(plan: Any) -> dict[str, Any] | None:
    """将 PlanNotebook 当前计划序列化为前端可消费结构。"""
    if plan is None:
        return None

    subtasks: list[dict[str, Any]] = []
    for index, item in enumerate(getattr(plan, "subtasks", []) or []):
        subtasks.append(
            {
                "index": index,
                "id": str(getattr(item, "id", "") or ""),
                "name": str(getattr(item, "name", "") or ""),
                "description": str(getattr(item, "description", "") or ""),
                "state": str(getattr(item, "state", "") or ""),
                "expected_outcome": str(getattr(item, "expected_outcome", "") or ""),
                "outcome": str(getattr(item, "outcome", "") or ""),
                "created_at": str(getattr(item, "created_at", "") or ""),
                "finished_at": str(getattr(item, "finished_at", "") or ""),
            }
        )

    return {
        "id": str(getattr(plan, "id", "") or ""),
        "name": str(getattr(plan, "name", "") or ""),
        "description": str(getattr(plan, "description", "") or ""),
        "expected_outcome": str(getattr(plan, "expected_outcome", "") or ""),
        "outcome": str(getattr(plan, "outcome", "") or ""),
        "state": str(getattr(plan, "state", "") or ""),
        "created_at": str(getattr(plan, "created_at", "") or ""),
        "finished_at": str(getattr(plan, "finished_at", "") or ""),
        "subtasks": subtasks,
    }


def _build_plan_event_delta(
    prev_plan: dict[str, Any] | None,
    curr_plan: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """根据前后 plan 快照生成结构化事件类型与变化明细。"""
    if prev_plan is None and curr_plan is not None:
        return (
            "plan_created",
            {
                "plan_id": str(curr_plan.get("id") or ""),
                "plan_name": str(curr_plan.get("name") or ""),
                "state": str(curr_plan.get("state") or ""),
            },
        )

    if prev_plan is not None and curr_plan is None:
        prev_state = str(prev_plan.get("state") or "")
        event_type = "plan_done" if prev_state == "done" else "plan_abandoned"
        return (
            event_type,
            {
                "plan_id": str(prev_plan.get("id") or ""),
                "plan_name": str(prev_plan.get("name") or ""),
                "state": prev_state,
                "outcome": str(prev_plan.get("outcome") or ""),
            },
        )

    if prev_plan is None or curr_plan is None:
        return ("plan_revised", {})

    prev_state = str(prev_plan.get("state") or "")
    curr_state = str(curr_plan.get("state") or "")
    if curr_state != prev_state:
        if curr_state == "done":
            return (
                "plan_done",
                {
                    "plan_id": str(curr_plan.get("id") or ""),
                    "plan_name": str(curr_plan.get("name") or ""),
                    "previous_state": prev_state,
                    "state": curr_state,
                    "outcome": str(curr_plan.get("outcome") or ""),
                },
            )
        if curr_state == "abandoned":
            return (
                "plan_abandoned",
                {
                    "plan_id": str(curr_plan.get("id") or ""),
                    "plan_name": str(curr_plan.get("name") or ""),
                    "previous_state": prev_state,
                    "state": curr_state,
                    "outcome": str(curr_plan.get("outcome") or ""),
                },
            )

    prev_subtasks = prev_plan.get("subtasks") if isinstance(prev_plan.get("subtasks"), list) else []
    curr_subtasks = curr_plan.get("subtasks") if isinstance(curr_plan.get("subtasks"), list) else []

    min_len = min(len(prev_subtasks), len(curr_subtasks))
    for idx in range(min_len):
        before = prev_subtasks[idx] if isinstance(prev_subtasks[idx], dict) else {}
        after = curr_subtasks[idx] if isinstance(curr_subtasks[idx], dict) else {}
        before_state = str(before.get("state") or "")
        after_state = str(after.get("state") or "")
        if before_state != after_state:
            if after_state == "in_progress":
                return (
                    "subtask_activated",
                    {
                        "subtask_idx": idx,
                        "subtask_id": str(after.get("id") or ""),
                        "subtask_name": str(after.get("name") or ""),
                        "previous_state": before_state,
                        "state": after_state,
                    },
                )
            if after_state == "done":
                return (
                    "subtask_done",
                    {
                        "subtask_idx": idx,
                        "subtask_id": str(after.get("id") or ""),
                        "subtask_name": str(after.get("name") or ""),
                        "previous_state": before_state,
                        "state": after_state,
                        "outcome": str(after.get("outcome") or ""),
                    },
                )
            return (
                "plan_revised",
                {
                    "subtask_idx": idx,
                    "subtask_id": str(after.get("id") or ""),
                    "subtask_name": str(after.get("name") or ""),
                    "previous_state": before_state,
                    "state": after_state,
                },
            )

    if len(prev_subtasks) != len(curr_subtasks):
        return (
            "plan_revised",
            {
                "subtasks_count_before": len(prev_subtasks),
                "subtasks_count_after": len(curr_subtasks),
            },
        )

    for idx in range(min_len):
        before = prev_subtasks[idx] if isinstance(prev_subtasks[idx], dict) else {}
        after = curr_subtasks[idx] if isinstance(curr_subtasks[idx], dict) else {}
        if (
            str(before.get("name") or "") != str(after.get("name") or "")
            or str(before.get("description") or "") != str(after.get("description") or "")
            or str(before.get("expected_outcome") or "") != str(after.get("expected_outcome") or "")
        ):
            return (
                "plan_revised",
                {
                    "subtask_idx": idx,
                    "subtask_id": str(after.get("id") or ""),
                    "subtask_name": str(after.get("name") or ""),
                    "state": str(after.get("state") or ""),
                },
            )

    return (
        "plan_revised",
        {
            "plan_id": str(curr_plan.get("id") or ""),
            "plan_name": str(curr_plan.get("name") or ""),
            "state": curr_state,
        },
    )


def _build_plan_reply_fallback(planner_events: list[dict[str, Any]]) -> str:
    """当 agent 无最终文本回复时，用计划执行产物回填用户可读结果。"""
    if not planner_events:
        return ""

    latest_plan: dict[str, Any] | None = None
    for event in reversed(planner_events):
        plan = event.get("plan")
        if isinstance(plan, dict):
            latest_plan = plan
            break
    if latest_plan is None:
        return ""

    lines: list[str] = []
    plan_name = str(latest_plan.get("name") or "").strip()
    plan_state = str(latest_plan.get("state") or "").strip()
    plan_outcome = str(latest_plan.get("outcome") or "").strip()

    if plan_name:
        lines.append(f"计划：{plan_name}")
    if plan_state:
        lines.append(f"状态：{plan_state}")
    if plan_outcome:
        lines.append(f"计划结果：{plan_outcome}")

    subtasks = latest_plan.get("subtasks")
    if isinstance(subtasks, list):
        done_items: list[str] = []
        for item in subtasks:
            if not isinstance(item, dict):
                continue
            if str(item.get("state") or "") != "done":
                continue
            name = str(item.get("name") or "").strip() or "未命名子任务"
            outcome = str(item.get("outcome") or "").strip()
            if outcome:
                done_items.append(f"- {name}: {outcome}")
            else:
                done_items.append(f"- {name}")
        if done_items:
            lines.append("已完成子任务：")
            lines.extend(done_items)

    return "\n".join(lines).strip()


def _parse_chat_command(task: str) -> tuple[str, str]:
    """解析 chat 指令与正文。"""
    raw = (task or "").strip()
    if not raw:
        return "message", ""
    parts = raw.split(maxsplit=1)
    head = parts[0].strip().lower()
    tail = parts[1].strip() if len(parts) > 1 else ""

    if head in {"/new", "new"}:
        return "new", tail
    if head in {"/interrupt", "interrupt", "/stop", "stop"}:
        return "interrupt", tail
    if head in {"/exit", "exit", "/quit", "quit", "退出"}:
        return "exit", tail
    return "message", raw


async def _run_gateway_chat_task(
    task: str,
    context_id: str | None = None,
    web_search_enabled: bool = True,
    progress_callback: PlannerProgressCallback | None = None,
) -> dict[str, Any]:
    """执行 gateway chat 模式单轮任务。"""
    command, content = _parse_chat_command(task)
    handle = await _CHAT_SESSION_MANAGER.resolve_session(
        context_id,
        force_new=(command == "new"),
    )

    agent = create_chat_agent(web_search_enabled=web_search_enabled)
    await _CHAT_SESSION_MANAGER.load_agent_state(handle, agent)
    introduced = bool(handle.meta.get("introduced", False))
    planner_events: list[dict[str, Any]] = []
    planner_event_seq = 0
    planner_prev_snapshot: dict[str, Any] | None = None
    planner_hook_name = f"gateway-chat-plan-{uuid.uuid4().hex}"
    plan_notebook = getattr(agent, "plan_notebook", None)

    async def _on_plan_change(_: Any, plan: Any) -> None:
        nonlocal planner_event_seq, planner_prev_snapshot
        curr_snapshot = _serialize_plan_for_monitor(plan)
        event_type, delta = _build_plan_event_delta(planner_prev_snapshot, curr_snapshot)
        planner_event_seq += 1
        plan_id = ""
        if isinstance(curr_snapshot, dict):
            plan_id = str(curr_snapshot.get("id") or "")
        elif isinstance(planner_prev_snapshot, dict):
            plan_id = str(planner_prev_snapshot.get("id") or "")

        event_item = {
            "event_key": f"{plan_id or 'plan'}:{planner_event_seq}",
            "event_type": event_type,
            "event_at": _CHAT_SESSION_MANAGER._utc_now_iso(),
            "plan": curr_snapshot,
            "delta": delta,
        }
        planner_events.append(event_item)
        if len(planner_events) > 120:
            del planner_events[:-120]
        planner_prev_snapshot = curr_snapshot
        await _emit_progress(
            progress_callback,
            {
                "channel": "planner_monitor",
                "session_id": handle.session_id,
                "mode": "chat",
                "planner": {
                    "enabled": True,
                    "latest_event": event_item,
                    "events": planner_events[-40:],
                    "current_plan": curr_snapshot,
                },
            },
        )

    if plan_notebook is not None:
        try:
            plan_notebook.register_plan_change_hook(planner_hook_name, _on_plan_change)
        except Exception:
            logger.warning("Failed to register chat planner hook", exc_info=True)

    def _cleanup_planner_hook() -> None:
        if plan_notebook is None:
            return
        try:
            plan_notebook.remove_plan_change_hook(planner_hook_name)
        except Exception:
            pass

    await _emit_progress(
        progress_callback,
        {
            "channel": "planner_monitor",
            "session_id": handle.session_id,
            "mode": "chat",
            "planner": {
                "enabled": bool(plan_notebook is not None),
                "latest_event": None,
                "events": [],
                "current_plan": _serialize_plan_for_monitor(getattr(plan_notebook, "current_plan", None)),
            },
        },
    )
    planner_prev_snapshot = _serialize_plan_for_monitor(getattr(plan_notebook, "current_plan", None))

    # /interrupt：优先中断当前活跃任务；如果没有活跃任务，走默认中断处理回复。
    if command == "interrupt":
        interrupted = await _CHAT_SESSION_MANAGER.interrupt_session(handle.session_id)
        if interrupted:
            assistant_text = "已收到中断指令，正在尝试中断当前回复。"
        else:
            interrupt_msg = await agent.handle_interrupt()
            assistant_text = _extract_response_text(interrupt_msg) or "已收到中断指令。"
        _CHAT_SESSION_MANAGER.append_turn_history(
            handle=handle,
            user_text=task,
            assistant_text=assistant_text,
            command="interrupt",
        )
        await _CHAT_SESSION_MANAGER.save_agent_state(
            handle,
            agent,
            command="interrupt",
            introduced=introduced,
        )
        _cleanup_planner_hook()
        return {
            "reply": assistant_text,
            "mode": "chat",
            "files": [],
            "planner_monitor": {
                "enabled": bool(plan_notebook is not None),
                "events": planner_events[-40:],
                "current_plan": _serialize_plan_for_monitor(getattr(plan_notebook, "current_plan", None)),
            },
            "context_id": handle.session_id,
            "session_id": handle.session_id,
            "session": {
                "session_id": handle.session_id,
                "session_dir": str(handle.session_dir.resolve()),
                "is_new_session": handle.is_new_session,
                "resumed_from_latest": handle.resumed_from_latest,
                "command": "interrupt",
            },
        }

    # /exit|quit|退出：保存状态并返回告别文本。
    if command == "exit":
        assistant_text = "会话状态已保存。你可以稍后继续，或输入 /new 开启新会话。"
        _CHAT_SESSION_MANAGER.append_turn_history(
            handle=handle,
            user_text=task,
            assistant_text=assistant_text,
            command="exit",
        )
        await _CHAT_SESSION_MANAGER.save_agent_state(
            handle,
            agent,
            command="exit",
            introduced=introduced,
        )
        _cleanup_planner_hook()
        return {
            "reply": assistant_text,
            "mode": "chat",
            "files": [],
            "planner_monitor": {
                "enabled": bool(plan_notebook is not None),
                "events": planner_events[-40:],
                "current_plan": _serialize_plan_for_monitor(getattr(plan_notebook, "current_plan", None)),
            },
            "context_id": handle.session_id,
            "session_id": handle.session_id,
            "session": {
                "session_id": handle.session_id,
                "session_dir": str(handle.session_dir.resolve()),
                "is_new_session": handle.is_new_session,
                "resumed_from_latest": handle.resumed_from_latest,
                "command": "exit",
            },
        }

    user_visible_text = task
    effective_content = content if command == "new" else (content or task)
    if command == "new" and not effective_content:
        effective_content = ""

    auto_intro_text = ""
    if not introduced:
        auto_intro_text = "你好，我是 Seneschal 的 chat 助手 MobiChatBot，很高兴为你服务。"
        memory = getattr(agent, "memory", None)
        if memory is not None and hasattr(memory, "add"):
            await memory.add(
                Msg(
                    name="ChatAssistant",
                    content=auto_intro_text,
                    role="assistant",
                ),
            )
        _CHAT_SESSION_MANAGER.append_turn_history(
            handle=handle,
            user_text="",
            assistant_text=auto_intro_text,
            command="auto_intro",
        )

    # /new 且没有额外内容时，直接返回首条自动欢迎消息。
    if command == "new" and not effective_content:
        await _CHAT_SESSION_MANAGER.save_agent_state(
            handle,
            agent,
            command="new",
            introduced=True,
        )
        _cleanup_planner_hook()
        return {
            "reply": auto_intro_text or "你好，我是 Seneschal 的 chat 助手 MobiChatBot，很高兴为你服务。",
            "mode": "chat",
            "files": [],
            "planner_monitor": {
                "enabled": bool(plan_notebook is not None),
                "events": planner_events[-40:],
                "current_plan": _serialize_plan_for_monitor(getattr(plan_notebook, "current_plan", None)),
            },
            "context_id": handle.session_id,
            "session_id": handle.session_id,
            "session": {
                "session_id": handle.session_id,
                "session_dir": str(handle.session_dir.resolve()),
                "is_new_session": handle.is_new_session,
                "resumed_from_latest": handle.resumed_from_latest,
                "command": "new",
            },
        }

    msg = Msg(
        name="User",
        content=effective_content,
        role="user",
    )

    try:
        reply_task = asyncio.create_task(agent(msg))
        await _CHAT_SESSION_MANAGER.register_active_reply(handle.session_id, agent, reply_task)
        try:
            response = await reply_task
        finally:
            await _CHAT_SESSION_MANAGER.unregister_active_reply(handle.session_id, reply_task)
    finally:
        _cleanup_planner_hook()

    assistant_text = _extract_response_text(response)
    reply_fallback = ""
    if not assistant_text:
        reply_fallback = _build_plan_reply_fallback(planner_events)

    core_reply = assistant_text or reply_fallback
    final_reply = core_reply
    if auto_intro_text and core_reply:
        final_reply = f"{auto_intro_text}\n\n{core_reply}"
    elif auto_intro_text and not core_reply:
        final_reply = auto_intro_text

    introduced_after = introduced or bool(auto_intro_text) or bool(core_reply)
    assistant_for_history = assistant_text or reply_fallback

    _CHAT_SESSION_MANAGER.append_turn_history(
        handle=handle,
        user_text=user_visible_text,
        assistant_text=assistant_for_history,
        command="new" if command == "new" else "message",
    )
    await _CHAT_SESSION_MANAGER.save_agent_state(
        handle,
        agent,
        command="new" if command == "new" else "message",
        introduced=introduced_after,
    )
    return {
        "reply": final_reply,
        "reply_fallback": reply_fallback,
        "mode": "chat",
        "files": [],
        "planner_monitor": {
            "enabled": bool(plan_notebook is not None),
            "events": planner_events[-40:],
            "current_plan": _serialize_plan_for_monitor(getattr(plan_notebook, "current_plan", None)),
        },
        "context_id": handle.session_id,
        "session_id": handle.session_id,
        "session": {
            "session_id": handle.session_id,
            "session_dir": str(handle.session_dir.resolve()),
            "is_new_session": handle.is_new_session,
            "resumed_from_latest": handle.resumed_from_latest,
            "command": "new" if command == "new" else "message",
        },
    }


def _extract_response_text(response: Any) -> str:
    """提取 Agent 返回对象中的纯文本内容。

    参数说明：
        response: Agent 返回对象，兼容 `get_text_content` 或 `content` 块结构。
    返回值说明：
        str: 提取后的文本，若无可用文本则返回空字符串。
    """
    if response is None:
        return ""
    text = response.get_text_content() if hasattr(response, "get_text_content") else ""
    if text:
        return text
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        block_text = getattr(block, "text", "")
        if block_text:
            parts.append(block_text)
    return "\n".join(parts).strip()


def _collect_file_paths(text: str, output_path: str | None = None) -> list[Path]:
    """从回复文本与输出路径提示中收集文件路径并去重。"""
    paths: list[Path] = []
    if output_path:
        paths.append(Path(output_path).expanduser())

    for raw in re.findall(r"\[File\]\s+Wrote:\s*(.+)", text or ""):
        candidate = raw.strip()
        if candidate:
            paths.append(Path(candidate).expanduser())

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _build_file_entries(paths: list[Path]) -> list[dict[str, Any]]:
    """将文件路径列表转换为可序列化的文件元数据结构。"""
    entries: list[dict[str, Any]] = []
    for path in paths:
        try:
            resolved = path.resolve()
        except FileNotFoundError:
            resolved = path.absolute()
        if not resolved.exists() or not resolved.is_file():
            continue
        stat = resolved.stat()
        entries.append(
            {
                "path": str(resolved),
                "name": resolved.name,
                "size": stat.st_size,
            }
        )
    return entries


async def run_gateway_task(
    task: str,
    output_path: str | None = None,
    mode: str = "router",
    agent_hint: str | None = None,
    skill_hint: str | None = None,
    routing_strategy: str | None = None,
    context_id: str | None = None,
    web_search_enabled: bool = True,
    progress_callback: PlannerProgressCallback | None = None,
    job_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """通过编排器执行网关任务。

    功能描述：
        将网关层请求透传至 `run_orchestrated_task`，统一走多 Agent 路由与执行流程。
    参数说明：
        task: 用户任务文本。
        output_path: 可选输出路径提示。
        mode: 执行模式（默认 router）。
        agent_hint: 可选 Agent 选择提示。
        skill_hint: 可选技能提示。
        routing_strategy: 可选路由策略覆盖值。
        context_id: 可选上下文 ID（用于多轮扩展）。
        web_search_enabled: chat 模式下是否启用联网搜索工具。
    返回值说明：
        dict[str, Any]: 编排执行结果。
    """
    logger.info(
        "workflows.run_gateway_task mode=%s web_search_enabled=%s agent_hint=%s task_preview=%s",
        mode,
        web_search_enabled,
        agent_hint or "",
        (task or "")[:120].replace("\n", " "),
    )
    if (mode or "").strip().lower() == "chat":
        return await _run_gateway_chat_task(
            task=task,
            context_id=context_id,
            web_search_enabled=web_search_enabled,
            progress_callback=progress_callback,
        )

    return await run_orchestrated_task(
        task=task,
        output_path=output_path,
        mode=mode,
        agent_hint=agent_hint,
        skill_hint=skill_hint,
        routing_strategy=routing_strategy,
        context_id=context_id,
        job_context=job_context,
    )


async def run_demo_conversation() -> None:
    """运行演示对话流程。"""
    print("=" * 70)
    print("Seneschal 个人数据管家智能体系统")
    print("=" * 70)
    print()

    steward = create_steward_agent()
    user = create_user_agent()

    print("✓ 智能管家 Agent (Steward) 已创建")
    print("✓ 用户代理 Agent (User) 已创建")
    print()
    print("-" * 70)
    print("开始演示对话...")
    print("-" * 70)
    print()

    preset_message = "开始今日的数据整理和分析，给出最近的待办事项。"
    print(f"[User]: {preset_message}")
    print()

    msg = Msg(
        name="User",
        content=preset_message,
        role="user",
    )

    print("[Steward 正在思考和执行...]")
    print("-" * 70)

    try:
        response = await steward(msg)

        print("-" * 70)
        print("[Steward 回复]:")
        print(response.get_text_content() if response else "（无回复）")
        print("-" * 70)

    except Exception as e:
        print(f"[错误] Agent 执行出错: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("=" * 70)
    print("演示结束")
    print("=" * 70)


async def run_interactive_mode() -> None:
    """运行交互式对话模式。"""
    print("=" * 70)
    print("Seneschal 个人数据管家智能体系统 - 交互模式")
    print("=" * 70)
    print()
    print("提示: 输入 'exit' 或 'quit' 退出对话")
    print()

    steward = create_steward_agent()
    user = create_user_agent()

    print("✓ 系统初始化完成")
    print("-" * 70)
    print()

    while True:
        try:
            msg = await user(None)

            user_text = msg.get_text_content() if msg else ""
            if user_text.lower() in ["exit", "quit", "退出"]:
                print("再见！")
                break

            print()
            await steward(msg)

            print()
            print("-" * 70)
            print()

        except KeyboardInterrupt:
            print("\n用户中断，退出程序。")
            break
        except Exception as e:
            print(f"[错误] {e}")
            continue


async def run_agent_task(
    task: str,
    output_path: str | None = None,
    mode: str = "router",
    agent_hint: str | None = None,
    skill_hint: str | None = None,
    routing_strategy: str | None = None,
    context_id: str | None = None,
) -> None:
    """运行通用 Agent 任务，默认使用智能路由多智能体编排。"""
    print("=" * 70)
    print("Seneschal Agent Task")
    print("=" * 70)
    print()

    print("[Orchestrator 正在路由与执行...]")
    print("-" * 70)
    logger.info("workflows.run_agent_task mode=%s agent_hint=%s task_preview=%s",
                mode, agent_hint or "", (task or "")[:120].replace("\n", " "))

    try:
        result = await run_orchestrated_task(
            task=task,
            output_path=output_path,
            mode=mode,
            agent_hint=agent_hint,
            skill_hint=skill_hint,
            routing_strategy=routing_strategy,
            context_id=context_id,
        )
        print("-" * 70)
        print("[Orchestrator 回复]:")
        text = str(result.get("reply") or "")
        if not text:
            text = "（无文本回复，可能是空工具调用）"
        print(text)

        trace = result.get("routing_trace") if isinstance(result, dict) else None
        if isinstance(trace, dict):
            print("-" * 70)
            print("[Routing Trace]:")
            print(str(trace))
        print("-" * 70)
    except Exception as e:
        print(f"[错误] Agent 执行出错: {e}")
        import traceback
        traceback.print_exc()

    print("=" * 70)


async def main() -> None:
    """主入口函数。"""
    import argparse

    parser = argparse.ArgumentParser(description="Seneschal workflow entrypoint")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Run interactive chat mode",
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Run daily collection workflow",
    )
    parser.add_argument(
        "--daily-trigger",
        default="daily",
        help="Trigger name used to filter daily tasks",
    )
    parser.add_argument(
        "--agent-task",
        help="Run an agent task with tool usage",
    )
    parser.add_argument(
        "--output",
        help="Optional output path hint for agent tasks",
    )
    parser.add_argument(
        "--mode",
        default="router",
        help="Task execution mode: router/intelligent or legacy worker/steward/auto",
    )
    parser.add_argument(
        "--agent-hint",
        help="Optional forced agent hint (worker/steward)",
    )
    parser.add_argument(
        "--skill-hint",
        help="Optional skill hint (single skill or comma separated skill names)",
    )
    parser.add_argument(
        "--routing-strategy",
        help="Optional routing strategy override",
    )
    parser.add_argument(
        "--context-id",
        help="Optional context id for future multi-turn orchestration",
    )
    args = parser.parse_args()

    if args.daily:
        print("=" * 70)
        print("Seneschal Daily Loop")
        print("=" * 70)
        result = await run_daily_tasks(args.daily_trigger)
        print("-" * 70)
        print(f"Run ID: {result['run_id']}")
        print(f"Tasks executed: {result['task_count']}")
        print("-" * 70)
    elif args.interactive:
        await run_interactive_mode()
    elif args.agent_task:
        await run_agent_task(
            args.agent_task,
            args.output,
            mode=args.mode,
            agent_hint=args.agent_hint,
            skill_hint=args.skill_hint,
            routing_strategy=args.routing_strategy,
            context_id=args.context_id,
        )
    else:
        await run_demo_conversation()
