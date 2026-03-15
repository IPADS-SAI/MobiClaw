from __future__ import annotations

from pathlib import Path

from mobiclaw.mobile.executor import MobileExecutor


class _CaptureMobiagentProvider:
    init_kwargs: dict | None = None

    def __init__(self, **kwargs):
        _CaptureMobiagentProvider.init_kwargs = dict(kwargs)
        self.run_dir = kwargs["run_dir"]

    def execute(self):
        run_dir = Path(self.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        return {"status": "completed", "step_count": 1, "message": "ok"}


def test_mobiagent_env_connectivity_config_is_passed_to_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("MOBILE_PROVIDER", "mobiagent")
    monkeypatch.setenv("MOBILE_DEVICE_TYPE", "mock")
    monkeypatch.setenv("MOBILE_MOBIAGENT_SERVER_IP", "166.111.53.96")
    monkeypatch.setenv("MOBILE_MOBIAGENT_DECIDER_PORT", "7003")
    monkeypatch.setenv("MOBILE_MOBIAGENT_GROUNDER_PORT", "7004")
    monkeypatch.setenv("MOBILE_MOBIAGENT_PLANNER_PORT", "7002")
    monkeypatch.setattr(
        "mobiclaw.mobile.executor.get_provider_class",
        lambda name: _CaptureMobiagentProvider if name == "mobiagent" else None,
    )

    result = MobileExecutor().run(task="检查配置连通性", output_dir=str(tmp_path), provider=None)

    assert result.success is True
    assert _CaptureMobiagentProvider.init_kwargs is not None
    assert _CaptureMobiagentProvider.init_kwargs["service_ip"] == "166.111.53.96"
    assert _CaptureMobiagentProvider.init_kwargs["decider_port"] == 7003
    assert _CaptureMobiagentProvider.init_kwargs["grounder_port"] == 7004
    assert _CaptureMobiagentProvider.init_kwargs["planner_port"] == 7002
    assert _CaptureMobiagentProvider.init_kwargs["api_base"] == ""
