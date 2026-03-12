# -*- coding: utf-8 -*-
"""Seneschal 定时任务调度模块。

提供定时/周期任务的意图检测、JSON 文件持久化存储与 APScheduler 集成。
支持 cron（周期）和 once（单次）两种调度类型。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .agents import create_openai_model, _extract_text_from_model_response
from .config import SCHEDULE_CONFIG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Schedule intent detection (LLM-based)
# ---------------------------------------------------------------------------

_SCHEDULE_HINT_PATTERNS = [
    r"每[天日周月年]",
    r"每隔",
    r"定[时期]",
    r"[早晚]上?\s*\d",
    r"\d+[点时]\d*[分]?.*(?:执行|运行|提醒|创建|搜|整理|汇总|发送|生成)",
    r"(?:周|星期)[一二三四五六日天].*(?:执行|运行|提醒|创建|搜|整理|汇总|发送|生成)",
    r"(?:上午|下午|凌晨|中午).*(?:执行|运行|提醒|创建|搜|整理|汇总|发送|生成)",
    r"(?:明天|后天|大后天|下周|下个?月)",
    r"every\s+(?:day|week|month|hour|minute)",
    r"(?:daily|weekly|monthly|hourly)",
    r"at\s+\d+:\d+",
    r"(?:tomorrow|next\s+\w+day)",
    r"cron",
]


def _has_schedule_hints(text: str) -> bool:
    """快速预检：文本中是否包含可能的定时关键词。"""
    for pattern in _SCHEDULE_HINT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


_DETECT_PROMPT_TEMPLATE = """\
你是一个定时任务意图解析器。分析用户消息是否包含"在特定时间"或"按周期"执行任务的意图。

当前时间: {now}

注意区分：
- "帮我搜集新闻" -> 立即执行，不是定时任务
- "每天帮我搜集新闻" -> 定时任务（周期）
- "明天早上8点帮我搜集新闻" -> 定时任务（单次）
- "每周一提醒我开会" -> 定时任务（周期）

如果不是定时任务，只输出:
{{"is_scheduled": false}}

如果是定时任务，输出:
{{
  "is_scheduled": true,
  "core_task": "核心任务描述，需要去除时间相关的描述",
  "schedule_type": "once 或 cron",
  "cron_expr": "分 时 日 月 周几（仅 cron 类型，周几用 mon/tue/wed/thu/fri/sat/sun）",
  "run_at": "ISO 8601 datetime（仅 once 类型，如 2025-03-15T08:00:00）",
  "human_description": "人类可读的时间描述"
}}

cron_expr 中如果用户没有指定具体时间，默认使用早上 8:00。

示例:
用户: "每天帮我搜集新闻"
{{"is_scheduled":true,"core_task":"搜集新闻","schedule_type":"cron","cron_expr":"0 8 * * *","run_at":null,"human_description":"每天早上8:00"}}

用户: "每周一8:00创建本周安排"
{{"is_scheduled":true,"core_task":"创建本周安排","schedule_type":"cron","cron_expr":"0 8 * * mon","run_at":null,"human_description":"每周一早上8:00"}}

用户: "明天下午3点提醒我开会"
{{"is_scheduled":true,"core_task":"提醒我开会","schedule_type":"once","cron_expr":null,"run_at":"{tomorrow_3pm}","human_description":"明天下午3:00"}}

用户: "工作日每天下午6点总结当天工作"
{{"is_scheduled":true,"core_task":"总结当天工作","schedule_type":"cron","cron_expr":"0 18 * * mon-fri","run_at":null,"human_description":"工作日每天下午6:00"}}

用户: "帮我搜一下最新的AI论文"
{{"is_scheduled":false}}

只输出 JSON，不要输出其他文本。"""


async def detect_schedule_intent(
    task_text: str
) -> ScheduleDetectionResult:
    """通过 LLM 检测用户消息中的定时任务意图。
    """
    # if not _has_schedule_hints(task_text):
    #     return ScheduleDetectionResult(is_scheduled=False)

    try:
        now = datetime.now()
        tomorrow_3pm = (now + timedelta(days=1)).replace(
            hour=15, minute=0, second=0, microsecond=0,
        )

        sys_prompt = _DETECT_PROMPT_TEMPLATE.format(
            now=now.strftime("%Y-%m-%d %H:%M:%S"),
            tomorrow_3pm=tomorrow_3pm.strftime("%Y-%m-%dT%H:%M:%S"),
        )

        model = create_openai_model(stream=False, temperature=0)
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": task_text},
        ]
        response = await model(messages)

        raw_text = _extract_text_from_model_response(response)
        parsed = _parse_json_from_text(raw_text)
        if not isinstance(parsed, dict):
            logger.warning("Schedule detection: non-dict output: %s", raw_text[:200])
            return ScheduleDetectionResult(is_scheduled=False)

        if not parsed.get("is_scheduled"):
            return ScheduleDetectionResult(is_scheduled=False)

        return ScheduleDetectionResult(
            is_scheduled=True,
            core_task=str(parsed.get("core_task") or task_text).strip(),
            schedule_type=str(parsed.get("schedule_type") or "cron").strip().lower(),
            cron_expr=parsed.get("cron_expr"),
            run_at=parsed.get("run_at"),
            human_description=str(parsed.get("human_description") or "").strip(),
        )

    except Exception as exc:
        logger.warning(
            "Schedule intent detection failed, treating as non-scheduled: %s", exc,
        )
        return ScheduleDetectionResult(is_scheduled=False)


def _parse_json_from_text(text: str) -> dict[str, Any] | None:
    """从 LLM 输出中提取 JSON 对象（支持 markdown 代码块）。"""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    if "```" in raw:
        for chunk in raw.split("```"):
            candidate = chunk.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    left = raw.find("{")
    right = raw.rfind("}")
    if left >= 0 and right > left:
        try:
            parsed = json.loads(raw[left : right + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Persistent store (JSON file)
# ---------------------------------------------------------------------------


class ScheduledTaskStore:
    """基于 JSON 文件的定时任务持久化存储。"""

    def __init__(self, store_path: str | Path | None = None) -> None:
        configured = str(
            store_path or SCHEDULE_CONFIG["store_path"]
        ).strip()
        self._path = Path(configured).expanduser() if configured else Path("~/.seneschal/schedules.json").expanduser()
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


# ---------------------------------------------------------------------------
# Schedule manager (APScheduler integration)
# ---------------------------------------------------------------------------

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

    # -- internal ----------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Module-level active manager reference & lifecycle
# ---------------------------------------------------------------------------

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


