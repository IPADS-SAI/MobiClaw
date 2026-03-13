from __future__ import annotations

from pathlib import Path

import pytest

from seneschal.mobile.executor import MobileExecutor


class _DummyProvider:
    def __init__(self, **kwargs):
        self.run_dir = kwargs["run_dir"]

    def execute(self):
        run_dir = Path(self.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        index_file = run_dir / "execution_result.json"
        payload = {
            "status": "completed",
            "message": "ok",
            "schema_version": "seneschal_mobile_exec_v1",
            "run_dir": str(run_dir),
            "summary": {
                "status_hint": "completed",
                "step_count": 1,
                "action_count": 1,
                "final_screenshot_path": "",
                "elapsed_time": 0.1,
            },
            "artifacts": {"images": [], "hierarchies": [], "overlays": [], "logs": []},
            "history": {"actions": {"actions": []}, "reacts": [], "reasonings": []},
            "ocr": {"source": "hierarchy_xml", "by_step": [], "full_text": ""},
            "index_file": str(index_file),
        }
        index_file.write_text("{}", encoding="utf-8")
        return payload


def test_mobile_executor_run_success(monkeypatch, tmp_path):
    monkeypatch.setenv("MOBILE_PROVIDER", "qwen")
    monkeypatch.setenv("MOBILE_DEVICE_TYPE", "mock")
    monkeypatch.setenv("MOBILE_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "seneschal.mobile.executor.get_provider_class",
        lambda name: _DummyProvider if name == "qwen" else None,
    )

    result = MobileExecutor().run(task="打开微信", output_dir=str(tmp_path), provider=None)
    assert result.success is True
    assert result.execution["schema_version"] == "seneschal_mobile_exec_v1"
    assert Path(result.execution["index_file"]).exists()


def test_mobile_executor_unknown_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("MOBILE_PROVIDER", "unknown")
    monkeypatch.setenv("MOBILE_DEVICE_TYPE", "mock")
    with pytest.raises(ValueError):
        MobileExecutor().run(task="t", output_dir=str(tmp_path), provider=None)
