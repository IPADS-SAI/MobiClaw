from __future__ import annotations

import asyncio

from seneschal.mobile.types import MobileExecutionResult
from seneschal.tools import mobi


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


def test_call_mobi_collect_verified_success(monkeypatch):
    monkeypatch.setattr(mobi, "_EXECUTOR", _ExecutorOK())
    resp = asyncio.run(mobi.call_mobi_collect_verified("task"))
    assert resp.metadata["success"] is True
    assert resp.metadata["status_hint"] == "completed"
    assert "execution" in resp.metadata


def test_call_mobi_action_fallback_mock(monkeypatch):
    monkeypatch.setattr(mobi, "_EXECUTOR", _ExecutorFail())
    resp = asyncio.run(mobi.call_mobi_action("open_app", '{"app_name": "微信"}'))
    assert resp.metadata.get("mock") is True
