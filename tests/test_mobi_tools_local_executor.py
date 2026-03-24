from __future__ import annotations

import asyncio
from pathlib import Path

from mobiclaw.mobile.types import MobileExecutionResult
from mobiclaw.tools import mobi


class _ExecutorOK:
    def run(self, task: str, output_dir: str, provider=None):
        _ = (task, output_dir, provider)
        execution = {
            "schema_version": "seneschal_mobile_exec_v1",
            "run_dir": "tmp/run",
            "index_file": "tmp/run/execution_result.json",
            "summary": {
                "status_hint": "completed",
                "step_count": 2,
                "action_count": 2,
                "final_screenshot_path": "tmp/run/2.jpg",
            },
            "artifacts": {"images": ["tmp/run/1.jpg", "tmp/run/2.jpg"], "hierarchies": [], "overlays": [], "logs": []},
            "history": {"actions": {"actions": []}, "reacts": [], "reasonings": ["r1", "r2"]},
            "ocr": {"source": "hierarchy_xml", "by_step": [], "full_text": "ocr text"},
        }
        return MobileExecutionResult(success=True, message="ok", execution=execution)


class _ExecutorFail:
    def run(self, task: str, output_dir: str, provider=None):
        _ = (task, output_dir, provider)
        raise RuntimeError("boom")


class _ExecutorFlaky:
    def __init__(self):
        self.calls = 0

    def run(self, task: str, output_dir: str, provider=None):
        _ = (task, output_dir, provider)
        self.calls += 1
        if self.calls < 2:
            raise RuntimeError("transient")
        execution = {
            "schema_version": "seneschal_mobile_exec_v1",
            "run_dir": "tmp/run",
            "index_file": "tmp/run/execution_result.json",
            "summary": {
                "status_hint": "completed",
                "step_count": 1,
                "action_count": 1,
                "final_screenshot_path": "tmp/run/1.jpg",
            },
            "artifacts": {"images": ["tmp/run/1.jpg"], "hierarchies": [], "overlays": [], "logs": []},
            "history": {"actions": {"actions": []}, "reacts": [], "reasonings": ["r1"]},
            "ocr": {"source": "hierarchy_xml", "by_step": [], "full_text": "ocr text"},
        }
        return MobileExecutionResult(success=True, message="ok", execution=execution)


def test_call_mobi_collect_verified_success(monkeypatch):
    monkeypatch.setattr(mobi, "_EXECUTOR", _ExecutorOK())
    monkeypatch.setattr(mobi, "_mobile_execution_mode", lambda: "local")
    resp = asyncio.run(mobi.call_mobi_collect_verified("task"))
    assert resp.metadata["success"] is True
    assert resp.metadata["status_hint"] == "completed"
    assert resp.metadata["final_image_path"] == "tmp/run/2.jpg"
    assert "execution" in resp.metadata
    assert "ocr_text" not in resp.metadata
    assert "screenshot_path" not in resp.metadata
    assert len(resp.content) == 2
    assert resp.content[0]["type"] == "text"
    assert resp.content[1]["type"] == "image"
    assert resp.content[1]["source"]["url"] == "tmp/run/2.jpg"


def test_call_mobi_action_task_fallback_mock(monkeypatch):
    monkeypatch.setattr(mobi, "_EXECUTOR", _ExecutorFail())
    monkeypatch.setattr(mobi, "_mobile_execution_mode", lambda: "local")
    resp = asyncio.run(mobi.call_mobi_action_task("打开微信"))
    assert resp.metadata.get("mock") is True


def test_call_mobi_action_task_uses_natural_language_desc(monkeypatch):
    captured: dict[str, str] = {}

    class _ExecutorCapture(_ExecutorOK):
        def run(self, task: str, output_dir: str, provider=None):
            captured["task"] = task
            return super().run(task=task, output_dir=output_dir, provider=provider)

    monkeypatch.setattr(mobi, "_EXECUTOR", _ExecutorCapture())
    monkeypatch.setattr(mobi, "_mobile_execution_mode", lambda: "local")
    resp = asyncio.run(mobi.call_mobi_action_task("通过微信给小赵发送消息：记得注册外卖平台"))

    assert resp.metadata["success"] is True
    assert captured["task"] == "通过微信给小赵发送消息：记得注册外卖平台"
    assert resp.metadata["action_type"] == "natural_language_task"


def test_call_mobi_collect_verified_retries_when_configured(monkeypatch):
    executor = _ExecutorFlaky()
    monkeypatch.setattr(mobi, "_EXECUTOR", executor)
    monkeypatch.setattr(mobi, "_mobile_execution_mode", lambda: "local")
    resp = asyncio.run(mobi.call_mobi_collect_verified("task", max_retries=1))
    assert resp.metadata["success"] is True
    assert resp.metadata["attempt"] == 2
    assert executor.calls == 2
    assert "ocr_text" not in resp.metadata
    assert "screenshot_path" not in resp.metadata


def test_build_execution_metadata_keeps_current_contract():
    execution = {
        "run_dir": "tmp/run",
        "index_file": "tmp/run/execution_result.json",
        "summary": {
            "status_hint": "completed",
            "step_count": 2,
            "action_count": 2,
            "final_screenshot_path": "tmp/run/2.jpg",
        },
        "artifacts": {"images": ["tmp/run/1.jpg", "tmp/run/2.jpg"]},
        "history": {"reasonings": ["r1", "r2"]},
    }
    metadata = mobi._build_execution_metadata(execution)

    assert metadata["status_hint"] == "completed"
    assert metadata["final_image_path"] == "tmp/run/2.jpg"
    assert metadata["last_reasoning"] == "r2"
    assert metadata["run_dir"] == "tmp/run"
    assert "execution" in metadata


def test_build_collect_content_appends_image_block_for_local_file(tmp_path: Path):
    image_path = tmp_path / "final.jpg"
    image_path.write_bytes(b"\xff\xd8\xff\xd9")

    content = mobi._build_collect_content(
        task_desc="task",
        metadata={"status_hint": "completed", "last_reasoning": "done", "final_image_path": str(image_path)},
        success=True,
        message="ok",
        attempt=1,
        total_attempts=1,
    )

    assert len(content) == 2
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image"
    assert content[1]["source"]["url"] == str(image_path)
