from __future__ import annotations

from pathlib import Path

import pytest

from seneschal.mobile import executor as mobile_executor
from seneschal.mobile.interrupts import clear_interrupt, interruptible_sleep, request_interrupt


def test_interruptible_sleep_raises_promptly() -> None:
    clear_interrupt()
    request_interrupt()
    with pytest.raises(KeyboardInterrupt):
        interruptible_sleep(1.0)
    clear_interrupt()


def test_mobile_executor_does_not_swallow_keyboard_interrupt(monkeypatch, tmp_path: Path) -> None:
    class _Cfg:
        name = "mock-provider"
        max_steps = 1
        draw = False
        api_base = ""
        api_key = ""
        model = ""
        temperature = 0.0
        extras = {}

    class _Runner:
        def __init__(self, **kwargs):  # noqa: ANN003
            _ = kwargs

        def execute(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(mobile_executor, "resolve_provider_config", lambda provider=None: _Cfg())
    monkeypatch.setattr(mobile_executor, "resolve_device_config", lambda: ("mock", None))
    monkeypatch.setattr(mobile_executor, "get_provider_class", lambda name: _Runner)

    with pytest.raises(KeyboardInterrupt):
        mobile_executor.MobileExecutor().run(task="interrupt me", output_dir=str(tmp_path), provider=None)
