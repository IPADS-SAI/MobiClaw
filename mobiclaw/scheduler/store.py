# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..config import SCHEDULE_CONFIG
from .models import ScheduledTask, logger


class ScheduledTaskStore:
    """基于 JSON 文件的定时任务持久化存储。"""

    def __init__(self, store_path: str | Path | None = None) -> None:
        configured = str(
            store_path or SCHEDULE_CONFIG["store_path"]
        ).strip()
        self._path = Path(configured).expanduser() if configured else Path("~/.mobiclaw/schedules.json").expanduser()
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def _load_raw(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            logger.warning("Failed to read schedule store: %s", self._path)
            return []

    def _save_raw(self, tasks: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(tasks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def add(self, task: ScheduledTask) -> None:
        async with self._lock:
            tasks = self._load_raw()
            tasks.append(asdict(task))
            self._save_raw(tasks)

    async def remove(self, schedule_id: str) -> bool:
        async with self._lock:
            tasks = self._load_raw()
            original_len = len(tasks)
            tasks = [t for t in tasks if t.get("schedule_id") != schedule_id]
            if len(tasks) == original_len:
                return False
            self._save_raw(tasks)
            return True

    async def update(self, schedule_id: str, updates: dict[str, Any]) -> bool:
        async with self._lock:
            tasks = self._load_raw()
            for task in tasks:
                if task.get("schedule_id") == schedule_id:
                    task.update(updates)
                    self._save_raw(tasks)
                    return True
            return False

    async def get(self, schedule_id: str) -> ScheduledTask | None:
        async with self._lock:
            for task_data in self._load_raw():
                if task_data.get("schedule_id") == schedule_id:
                    return _dict_to_scheduled_task(task_data)
            return None

    async def list_all(self) -> list[ScheduledTask]:
        async with self._lock:
            return [_dict_to_scheduled_task(t) for t in self._load_raw()]

    async def list_active(self) -> list[ScheduledTask]:
        all_tasks = await self.list_all()
        return [t for t in all_tasks if t.status == "active"]


def _dict_to_scheduled_task(data: dict[str, Any]) -> ScheduledTask:
    """安全地将 dict 转换为 ScheduledTask。"""
    return ScheduledTask(
        schedule_id=str(data.get("schedule_id") or ""),
        core_task=str(data.get("core_task") or ""),
        original_task=str(data.get("original_task") or ""),
        schedule_type=str(data.get("schedule_type") or "cron"),
        cron_expr=data.get("cron_expr"),
        run_at=data.get("run_at"),
        human_description=str(data.get("human_description") or ""),
        status=str(data.get("status") or "active"),
        created_at=str(data.get("created_at") or ""),
        last_run_at=data.get("last_run_at"),
        next_run_at=data.get("next_run_at"),
        run_count=int(data.get("run_count") or 0),
        source=str(data.get("source") or ""),
        mode=str(data.get("mode") or "router"),
        agent_hint=data.get("agent_hint"),
        skill_hint=data.get("skill_hint"),
        routing_strategy=data.get("routing_strategy"),
        web_search_enabled=bool(data.get("web_search_enabled", True)),
        job_context=(
            data.get("job_context")
            if isinstance(data.get("job_context"), dict)
            else {}
        ),
        last_job_ids=(
            data.get("last_job_ids")
            if isinstance(data.get("last_job_ids"), list)
            else []
        ),
    )
