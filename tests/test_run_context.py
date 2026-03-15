from __future__ import annotations

import json
from pathlib import Path

from mobiclaw import run_context
from mobiclaw.run_context import RunContext, create_run_context


def test_create_run_context_writes_run_start_event(tmp_path: Path) -> None:
    ctx = create_run_context(log_dir=tmp_path)

    assert ctx.run_id
    assert len(ctx.events) == 1
    assert ctx.events[0]["type"] == "run_start"

    log_path = tmp_path / f"{ctx.run_id}.jsonl"
    assert log_path.exists()
    rows = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["type"] == "run_start"


def test_log_event_fallback_to_info_level(monkeypatch, tmp_path: Path) -> None:
    recorded: list[str] = []

    def _fake_info(message: str, *args) -> None:  # noqa: ANN001
        del args
        recorded.append(message)

    monkeypatch.setattr(run_context.logger, "info", _fake_info)

    ctx = RunContext(run_id="r1", started_at="t0", log_path=tmp_path / "r1.jsonl")
    event = ctx.log_event("custom", {"k": "v"}, level="not-real")

    assert event["type"] == "custom"
    assert recorded

    rows = [line for line in (tmp_path / "r1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    parsed = json.loads(rows[0])
    assert parsed["payload"] == {"k": "v"}


def test_log_event_without_log_path_only_keeps_memory() -> None:
    ctx = RunContext(run_id="r2", started_at="t0", log_path=None)
    ctx.log_event("evt", {"ok": True})

    assert len(ctx.events) == 1
    assert ctx.events[0]["type"] == "evt"
