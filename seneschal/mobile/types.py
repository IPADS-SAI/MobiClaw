from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ProviderConfig:
    name: str
    api_base: str
    api_key: str
    model: str
    temperature: float
    max_steps: int
    draw: bool
    extras: dict[str, Any]


@dataclass(slots=True)
class MobileExecutionResult:
    success: bool
    message: str
    execution: dict[str, Any]
