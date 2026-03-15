# -*- coding: utf-8 -*-

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .helpers import _build_trigger
from .models import (
    ScheduleDetectionResult,
    ScheduledJobExecutor,
    ScheduledTask,
    logger,
)
from .store import ScheduledTaskStore

_MAX_STORED_JOB_IDS = 20


class ScheduleManager:
    """基于 APScheduler 的定时任务管理器，配合 JSON 文件持久化。

    生命周期：
        lifespan start  -> manager.start()  (恢复持久化任务)
        lifespan end    -> manager.shutdown()
    """

    def __init__(
        self,
        *,
        store_path: str | Path | None = None,
        job_executor: ScheduledJobExecutor | None = None,
    ) -> None:
        self._store = ScheduledTaskStore(store_path)
        self._scheduler = AsyncIOScheduler()
        self._job_executor = job_executor
        self._started = False

    @property
    def store(self) -> ScheduledTaskStore:
        return self._store

    def set_job_executor(self, executor: ScheduledJobExecutor) -> None:
        self._job_executor = executor

    async def start(self) -> None:
        """启动调度器并从持久化存储恢复任务。"""
        if self._started:
            return
        self._scheduler.start()
        self._started = True
        await self._restore_from_store()
        logger.info("ScheduleManager started, store=%s", self._store.path)

    async def shutdown(self) -> None:
        """关闭调度器。"""
        if not self._started:
            return
        self._scheduler.shutdown(wait=False)
        self._started = False
        logger.info("ScheduleManager shut down")

    async def add_scheduled_task(
        self,
        *,
        detection: ScheduleDetectionResult,
        original_task: str,
        source: str = "api",
        mode: str = "router",
        agent_hint: str | None = None,
        skill_hint: str | None = None,
        routing_strategy: str | None = None,
        web_search_enabled: bool = True,
        job_context: dict[str, Any] | None = None,
    ) -> ScheduledTask:
        """根据 LLM 检测结果创建定时任务。"""
        schedule_id = uuid.uuid4().hex
        now_iso = datetime.now(timezone.utc).isoformat()

        task = ScheduledTask(
            schedule_id=schedule_id,
            core_task=detection.core_task,
            original_task=original_task,
            schedule_type=detection.schedule_type,
            cron_expr=detection.cron_expr,
            run_at=detection.run_at,
            human_description=detection.human_description,
            status="active",
            created_at=now_iso,
            source=source,
            mode=mode,
            agent_hint=agent_hint,
            skill_hint=skill_hint,
            routing_strategy=routing_strategy,
            web_search_enabled=web_search_enabled,
            job_context=job_context or {},
        )

        trigger = _build_trigger(task)
        self._scheduler.add_job(
            self._fire_task,
            trigger=trigger,
            args=[schedule_id],
            id=schedule_id,
            name=f"{detection.core_task}({detection.human_description})",
            replace_existing=True,
        )

        await self._store.add(task)
        logger.info(
            "Scheduled task created: id=%s type=%s desc='%s'",
            schedule_id,
            detection.schedule_type,
            detection.human_description,
        )
        return task

    async def cancel_task(self, schedule_id: str) -> bool:
        """取消定时任务。"""
        try:
            self._scheduler.remove_job(schedule_id)
        except Exception:
            pass
        updated = await self._store.update(schedule_id, {"status": "cancelled"})
        if updated:
            logger.info("Scheduled task cancelled: %s", schedule_id)
        return updated

    async def list_tasks(self) -> list[ScheduledTask]:
        """列出所有定时任务，并从 APScheduler 刷新 next_run_at。"""
        tasks = await self._store.list_all()
        for task in tasks:
            if task.status == "active":
                aps_job = self._scheduler.get_job(task.schedule_id)
                if aps_job and aps_job.next_run_time:
                    task.next_run_at = aps_job.next_run_time.isoformat()
        return tasks

    async def _restore_from_store(self) -> None:
        """启动时从 JSON 恢复活跃任务到 APScheduler。"""
        active_tasks = await self._store.list_active()
        restored = 0
        for task in active_tasks:
            try:
                if task.schedule_type == "once" and task.run_at:
                    run_date = datetime.fromisoformat(task.run_at)
                    now = (
                        datetime.now(timezone.utc)
                        if run_date.tzinfo
                        else datetime.now()
                    )
                    if run_date < now:
                        await self._store.update(
                            task.schedule_id, {"status": "expired"},
                        )
                        logger.info(
                            "Expired past one-time task on restore: %s",
                            task.schedule_id,
                        )
                        continue

                trigger = _build_trigger(task)
                self._scheduler.add_job(
                    self._fire_task,
                    trigger=trigger,
                    args=[task.schedule_id],
                    id=task.schedule_id,
                    name=f"{task.core_task}({task.human_description})",
                    replace_existing=True,
                )
                restored += 1
            except Exception as exc:
                logger.warning(
                    "Failed to restore scheduled task %s: %s",
                    task.schedule_id,
                    exc,
                )
        logger.info("Restored %d/%d scheduled tasks from store", restored, len(active_tasks))

    async def _fire_task(self, schedule_id: str) -> None:
        """APScheduler 触发回调：执行定时任务。"""
        task = await self._store.get(schedule_id)
        if task is None or task.status != "active":
            logger.warning(
                "Scheduled task %s not found or inactive, skipping execution",
                schedule_id,
            )
            return

        if self._job_executor is None:
            logger.error(
                "No job executor configured, cannot run scheduled task %s",
                schedule_id,
            )
            return

        try:
            job_id = await self._job_executor(
                schedule_id=schedule_id,
                task=task.core_task,
                mode=task.mode,
                agent_hint=task.agent_hint,
                skill_hint=task.skill_hint,
                routing_strategy=task.routing_strategy,
                context_id=None,
                web_search_enabled=task.web_search_enabled,
                job_context=task.job_context,
            )

            now_iso = datetime.now(timezone.utc).isoformat()
            job_ids = list(task.last_job_ids or [])
            job_ids.append(job_id)
            if len(job_ids) > _MAX_STORED_JOB_IDS:
                job_ids = job_ids[-_MAX_STORED_JOB_IDS:]

            updates: dict[str, Any] = {
                "last_run_at": now_iso,
                "run_count": task.run_count + 1,
                "last_job_ids": job_ids,
            }
            if task.schedule_type == "once":
                updates["status"] = "completed"

            await self._store.update(schedule_id, updates)
            logger.info(
                "Scheduled task %s fired, job_id=%s (run #%d)",
                schedule_id,
                job_id,
                task.run_count + 1,
            )

        except Exception as exc:
            logger.exception(
                "Failed to execute scheduled task %s: %s", schedule_id, exc,
            )
