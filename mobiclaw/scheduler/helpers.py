# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .models import ScheduledTask


def _build_trigger(task: ScheduledTask) -> CronTrigger | DateTrigger:
    """根据任务配置构建 APScheduler 触发器。"""
    if task.schedule_type == "cron" and task.cron_expr:
        return _parse_cron_to_trigger(task.cron_expr)
    if task.schedule_type == "once" and task.run_at:
        run_date = datetime.fromisoformat(task.run_at)
        return DateTrigger(run_date=run_date)
    raise ValueError(
        f"Cannot build trigger: type={task.schedule_type} "
        f"cron={task.cron_expr} run_at={task.run_at}"
    )


def _parse_cron_to_trigger(cron_expr: str) -> CronTrigger:
    """将 5 字段 cron 表达式解析为 APScheduler CronTrigger。

    格式: 分 时 日 月 周几
    周几支持数字（APScheduler 约定 0=mon）或名称（mon/tue/...）。
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-field cron expression, got {len(parts)}: {cron_expr}")
    return CronTrigger(
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=parts[4],
    )
