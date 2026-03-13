# -*- coding: utf-8 -*-
"""定时任务 Worker Agent 工具函数（创建、查看、取消）。"""

from __future__ import annotations

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from ..scheduler import (
    detect_schedule_intent,
    get_active_manager,
)


async def list_scheduled_tasks() -> ToolResponse:
    """列出所有定时任务及其状态信息。

    Args:
        None.
    """
    manager = get_active_manager()
    if manager is None:
        return ToolResponse(
            content=[TextBlock(type="text", text="定时任务调度器未启用。")],
        )
    tasks = await manager.list_tasks()
    if not tasks:
        return ToolResponse(
            content=[TextBlock(type="text", text="当前没有任何定时任务。")],
        )
    items = []
    for t in tasks:
        items.append(
            f"- schedule_id: {t.schedule_id}\n"
            f"  任务: {t.core_task}\n"
            f"  状态: {t.status}\n"
            f"  类型: {t.schedule_type}\n"
            f"  描述: {t.human_description}\n"
            f"  cron: {t.cron_expr or 'N/A'}\n"
            f"  下次执行: {t.next_run_at or 'N/A'}"
        )
    text = f"共 {len(tasks)} 个定时任务:\n\n" + "\n\n".join(items)
    return ToolResponse(
        content=[TextBlock(type="text", text=text)],
    )


async def create_scheduled_task(
    task: str,
    time_description: str,
    *,
    bound_job_context: dict | None = None,
) -> ToolResponse:
    """创建定时任务。task 为核心任务描述，time_description 为自然语言时间描述。

    Args:
        task: 核心任务描述。
        time_description: 自然语言时间描述。
        bound_job_context: 可选任务上下文，透传给调度器作业上下文。
    """
    manager = get_active_manager()
    if manager is None:
        return ToolResponse(
            content=[TextBlock(type="text", text="定时任务调度器未启用。")],
        )
    detection = await detect_schedule_intent(f"{time_description}{task}")
    if not detection.is_scheduled:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"无法从 \"{time_description}\" 中解析出定时意图，请明确时间描述。")],
        )
    scheduled = await manager.add_scheduled_task(
        detection=detection,
        original_task=f"{time_description}{task}",
        source="agent",
        job_context=bound_job_context or {},
    )
    return ToolResponse(
        content=[TextBlock(type="text", text=(
            f"定时任务创建成功！\n"
            f"- schedule_id: {scheduled.schedule_id}\n"
            f"- 任务: {scheduled.core_task}\n"
            f"- 时间: {scheduled.human_description}\n"
            f"- 类型: {scheduled.schedule_type}\n"
            f"- cron: {scheduled.cron_expr or 'N/A'}"
        ))],
    )


async def cancel_scheduled_task(schedule_id: str) -> ToolResponse:
    """取消指定的定时任务。需要提供 schedule_id。

    Args:
        schedule_id: 待取消的定时任务 ID。
    """
    manager = get_active_manager()
    if manager is None:
        return ToolResponse(
            content=[TextBlock(type="text", text="定时任务调度器未启用。")],
        )
    success = await manager.cancel_task(schedule_id)
    if success:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"定时任务 {schedule_id} 已成功取消。")],
        )
    return ToolResponse(
        content=[TextBlock(type="text", text=f"未找到 schedule_id 为 {schedule_id} 的定时任务，取消失败。")],
    )
