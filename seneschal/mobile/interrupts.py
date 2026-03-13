# -*- coding: utf-8 -*-
"""Utilities for cooperative interruption of mobile task execution."""

from __future__ import annotations

import signal
import threading
import time

_INTERRUPT_EVENT = threading.Event()


def clear_interrupt() -> None:
    _INTERRUPT_EVENT.clear()


def request_interrupt() -> None:
    _INTERRUPT_EVENT.set()


def interrupt_requested() -> bool:
    return _INTERRUPT_EVENT.is_set()


def ensure_not_interrupted() -> None:
    if interrupt_requested():
        raise KeyboardInterrupt("Interrupted by user")


def interruptible_sleep(seconds: float, *, poll_interval: float = 0.1) -> None:
    remaining = max(0.0, float(seconds))
    if remaining <= 0:
        ensure_not_interrupted()
        return

    while remaining > 0:
        ensure_not_interrupted()
        chunk = min(remaining, max(0.01, poll_interval))
        start = time.monotonic()
        _INTERRUPT_EVENT.wait(chunk)
        ensure_not_interrupted()
        remaining -= time.monotonic() - start


def _handle_sigint(signum, frame) -> None:  # noqa: ANN001
    _ = (signum, frame)
    request_interrupt()
    raise KeyboardInterrupt


def install_signal_handlers() -> None:
    signal.signal(signal.SIGINT, _handle_sigint)
