# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class ScheduleDetectionResult:
    """LLM 解析定时意图的结果。"""

    is_scheduled: bool = False
    core_task: str = ""
    schedule_type: str = ""  # "once" | "cron"
    cron_expr: str | None = None  # "minute hour day month day_of_week"
    run_at: str | None = None  # ISO datetime for one-time tasks
    human_description: str = ""


@dataclass
class ScheduledTask:
    """定时任务持久化模型。"""

    schedule_id: str
    core_task: str
    original_task: str
    schedule_type: str  # "once" | "cron"
    cron_expr: str | None = None
    run_at: str | None = None
    human_description: str = ""
    status: str = "active"  # active | completed | cancelled | expired
    created_at: str = ""
    last_run_at: str | None = None
    next_run_at: str | None = None
    run_count: int = 0
    source: str = ""  # "api" | "feishu" | "chat"
    mode: str = "router"
    agent_hint: str | None = None
    skill_hint: str | None = None
    routing_strategy: str | None = None
    web_search_enabled: bool = True
    job_context: dict[str, Any] = field(default_factory=dict)
    last_job_ids: list[str] = field(default_factory=list)


ScheduledJobExecutor = Callable[..., Awaitable[str]]
