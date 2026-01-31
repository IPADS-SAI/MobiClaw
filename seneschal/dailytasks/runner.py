# -*- coding: utf-8 -*-
"""Daily task runner for Seneschal."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Any

from agentscope.tool import ToolResponse

from ..run_context import RunContext, create_run_context
from ..tools import call_mobi_collect, weknora_add_knowledge, weknora_rag_chat

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

    ctx.log_event("task_selection", {"trigger": trigger, "task_count": len(tasks)})

    collected: list[dict[str, Any]] = []
    for task in tasks:
        task_id = task.get("task_id", "unknown")
        prompt = build_task_prompt(task)
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

        weknora_add_knowledge(
            text_content,
            title=f"Seneschal {task_id}",
            metadata=metadata,
        )

        collected.append(
            {
                "task_id": task_id,
                "prompt": prompt,
                "content": text_content,
                "metadata": metadata,
            }
        )
        ctx.log_event("collect_done", {"task_id": task_id})

    analysis_query = (
        "请基于近期新增记录进行总结，输出："
        "1) 摘要 2) 待办 3) 风险提醒 4) 建议行动。"
        f"本次 run_id={ctx.run_id}。"
    )
    ctx.log_event("analyze_start", {"query": analysis_query})
    analysis = weknora_rag_chat(analysis_query)
    ctx.log_event("analyze_done", {"summary": tool_response_to_text(analysis)})

    return {
        "run_id": ctx.run_id,
        "task_count": len(tasks),
        "collected": collected,
        "analysis": analysis,
    }
