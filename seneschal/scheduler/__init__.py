# -*- coding: utf-8 -*-
"""Seneschal 定时任务调度模块。

提供定时/周期任务的意图检测、JSON 文件持久化存储与 APScheduler 集成。
支持 cron（周期）和 once（单次）两种调度类型。
"""

from .detection import (
    _DETECT_PROMPT_TEMPLATE,
    _SCHEDULE_HINT_PATTERNS,
    _has_schedule_hints,
    _parse_json_from_text,
    detect_schedule_intent,
)
from .helpers import _build_trigger, _parse_cron_to_trigger
from .lifecycle import get_active_manager, shutdown_scheduler, start_scheduler
from .manager import _MAX_STORED_JOB_IDS, ScheduleManager
from .models import ScheduleDetectionResult, ScheduledJobExecutor, ScheduledTask
from .store import ScheduledTaskStore, _dict_to_scheduled_task

__all__ = [
    "ScheduleDetectionResult",
    "ScheduledJobExecutor",
    "ScheduledTask",
    "ScheduledTaskStore",
    "ScheduleManager",
    "detect_schedule_intent",
    "get_active_manager",
    "shutdown_scheduler",
    "start_scheduler",
    "_DETECT_PROMPT_TEMPLATE",
    "_MAX_STORED_JOB_IDS",
    "_SCHEDULE_HINT_PATTERNS",
    "_build_trigger",
    "_dict_to_scheduled_task",
    "_has_schedule_hints",
    "_parse_cron_to_trigger",
    "_parse_json_from_text",
]
