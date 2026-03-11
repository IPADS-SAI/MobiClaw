# -*- coding: utf-8 -*-
"""Seneschal 运行上下文与事件日志工具。

核心功能：
- 为每次任务执行生成唯一 run_id；
- 将运行过程中的结构化事件写入内存并按 JSONL 追加落盘；
- 为上层工作流提供统一的运行追踪接口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """获取当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunContext:
    """单次运行上下文与轻量事件日志容器。"""

    run_id: str
    started_at: str
    log_path: Path | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def log_event(self, event_type: str, payload: dict[str, Any], level: str = "info") -> dict[str, Any]:
        event = {
            "run_id": self.run_id,
            "timestamp": _utc_now_iso(),
            "type": event_type,
            "level": level,
            "payload": payload,
        }
        self.events.append(event)
        log_fn = getattr(logger, level if level in {"debug", "info", "warning", "error", "critical"} else "info", logger.info)
        log_fn("run_context.event type=%s run_id=%s", event_type, self.run_id)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event


def create_run_context(log_dir: str | Path = "seneschal/logs") -> RunContext:
    """创建运行上下文并记录启动事件。

    功能描述：
        生成唯一 `run_id`，初始化 JSONL 日志路径，并写入 `run_start` 事件。
    参数说明：
        log_dir: 日志目录，支持字符串或 Path。
    返回值说明：
        RunContext: 可持续记录事件的运行上下文实例。
    异常说明：
        无显式抛出；文件系统错误由底层 I/O 在写入事件时抛出。
    """

    run_id = uuid.uuid4().hex
    log_path = Path(log_dir) / f"{run_id}.jsonl"
    ctx = RunContext(run_id=run_id, started_at=_utc_now_iso(), log_path=log_path)
    ctx.log_event("run_start", {"started_at": ctx.started_at})
    return ctx
