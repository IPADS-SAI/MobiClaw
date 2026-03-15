# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path

from .manager import ScheduleManager
from .models import ScheduledJobExecutor

_active_manager: ScheduleManager | None = None


def get_active_manager() -> ScheduleManager | None:
    """获取当前活跃的 ScheduleManager（可能为 None）。"""
    return _active_manager


async def start_scheduler(
    job_executor: ScheduledJobExecutor,
    store_path: str | Path | None = None,
) -> None:
    """初始化并启动 ScheduleManager（由 gateway lifespan 调用）。"""
    global _active_manager
    manager = ScheduleManager(store_path=store_path, job_executor=job_executor)
    await manager.start()
    _active_manager = manager


async def shutdown_scheduler() -> None:
    """关闭并清除 ScheduleManager。"""
    global _active_manager
    if _active_manager is not None:
        await _active_manager.shutdown()
        _active_manager = None
