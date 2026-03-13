# -*- coding: utf-8 -*-
"""Daily task runner for Seneschal."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import logging
from typing import Any

from agentscope.message import Msg
from agentscope.tool import ToolResponse

from ..agents import create_worker_agent
from ..run_context import RunContext, create_run_context
from ..tools import call_mobi_collect

logger = logging.getLogger(__name__)

_TASKS_PATH = Path(__file__).with_name("tasks") / "tasks.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_tasks(path: Path = _TASKS_PATH) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data.get("tasks", [])


def select_tasks(tasks: list[dict[str, Any]], trigger: str) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for task in tasks:
        if trigger in task.get("triggers", []):
            selected.append(task)
    return selected


def build_task_prompt(task: dict[str, Any]) -> str:
    steps = task.get("steps")
    if steps:
        return steps
    return task.get("description", "")


def tool_response_to_text(response: ToolResponse | None) -> str:
    if response is None:
        return ""
    parts: list[str] = []
    for block in response.content or []:
        text = getattr(block, "text", "")
        if text:
            parts.append(text)
    return "\n".join(parts)


async def run_daily_tasks(
    trigger: str = "daily",
    run_context: RunContext | None = None,
) -> dict[str, Any]:
    ctx = run_context or create_run_context()
    tasks = select_tasks(load_tasks(), trigger)
    logger.info("dailytasks.start trigger=%s task_count=%d run_id=%s", trigger, len(tasks), ctx.run_id)

    ctx.log_event("task_selection", {"trigger": trigger, "task_count": len(tasks)})

    collected: list[dict[str, Any]] = []
    collect_count = 0
    for task in tasks:
        task_id = task.get("task_id", "unknown")
        prompt = build_task_prompt(task)
        task_type = (task.get("task_type") or "collect").strip().lower()

        if task_type == "agent_task":
            ctx.log_event("agent_task_start", {"task_id": task_id, "prompt": prompt})
            worker = create_worker_agent()
            output_path = (task.get("output_path") or "").strip() or None
            msg_content = prompt or ""
            if output_path:
                msg_content += (
                    "\n\n输出文件路径: "
                    + output_path
                    + "\n如需落盘，请自行选择合适工具完成。"
                )
            response = await worker(Msg(name="User", content=msg_content, role="user"))
            text_content = response.get_text_content() if response else ""
            collected.append(
                {
                    "task_id": task_id,
                    "prompt": prompt,
                    "content": text_content,
                    "metadata": {
                        "run_id": ctx.run_id,
                        "task_id": task_id,
                        "task_type": task_type,
                        "trigger": trigger,
                        "timestamp": _utc_now_iso(),
                    },
                }
            )
            ctx.log_event("agent_task_done", {"task_id": task_id})
            continue

        ctx.log_event("collect_start", {"task_id": task_id, "prompt": prompt})
        response = await call_mobi_collect(prompt)
        text_content = tool_response_to_text(response)
        metadata = {
            "run_id": ctx.run_id,
            "task_id": task_id,
            "category": task.get("category"),
            "app": task.get("app"),
            "trigger": trigger,
            "timestamp": _utc_now_iso(),
        }
        if response and response.metadata:
            metadata["collect_metadata"] = response.metadata

        collect_count += 1

        collected.append(
            {
                "task_id": task_id,
                "prompt": prompt,
                "content": text_content,
                "metadata": metadata,
            }
        )
        ctx.log_event("collect_done", {"task_id": task_id})

    return {
        "run_id": ctx.run_id,
        "task_count": len(tasks),
        "collected": collected,
    }
