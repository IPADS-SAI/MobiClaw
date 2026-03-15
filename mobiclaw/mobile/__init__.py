from __future__ import annotations

from .types import MobileExecutionResult

__all__ = ["MobileExecutor", "MobileExecutionResult"]


def __getattr__(name: str):
    if name == "MobileExecutor":
        from .executor import MobileExecutor
        return MobileExecutor
    raise AttributeError(name)

