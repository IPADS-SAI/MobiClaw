# -*- coding: utf-8 -*-
"""Run context and logging utilities for Seneschal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json
import uuid
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunContext:
    """Represents a single run with lightweight event logging."""

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
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event


def create_run_context(log_dir: str | Path = "seneschal/logs") -> RunContext:
    """Create a run context with a unique run_id and optional JSONL logging."""

    run_id = uuid.uuid4().hex
    log_path = Path(log_dir) / f"{run_id}.jsonl"
    ctx = RunContext(run_id=run_id, started_at=_utc_now_iso(), log_path=log_path)
    ctx.log_event("run_start", {"started_at": ctx.started_at})
    return ctx
