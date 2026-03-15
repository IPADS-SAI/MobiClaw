# -*- coding: utf-8 -*-
"""mobiclaw.agents 的数据结构与基础工具。"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass
class AgentCapability:
    """描述单个 Agent 能力边界的结构化模型。"""

    name: str
    role: str
    strengths: list[str]
    typical_tasks: list[str]
    boundaries: list[str]


@dataclass
class CustomAgentDefinition:
    """配置文件驱动的自定义 Agent 定义。"""

    name: str
    display_name: str
    role: str
    system_prompt: str
    tools: list[str]
    strengths: list[str]
    typical_tasks: list[str]
    boundaries: list[str]
    model_name: str | None
    temperature: float | None
    max_iters: int


def _normalize_agent_name(raw: str) -> str:
    value = re.sub(r"[^0-9a-zA-Z_\-]+", "_", str(raw or "").strip().lower())
    return value.strip("_")


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            items.append(text)
    return items
