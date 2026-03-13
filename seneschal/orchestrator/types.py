# -*- coding: utf-8 -*-
"""orchestrator 的类型与常量定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_CYAN = "\033[96m"
ANSI_YELLOW = "\033[93m"
ANSI_GREEN = "\033[92m"
ANSI_RED = "\033[91m"


def _highlight_log(message: str, color: str = ANSI_CYAN) -> str:
    """格式化彩色高亮日志文本，便于终端观察关键编排节点。"""
    return f"{ANSI_BOLD}{color}{message}{ANSI_RESET}"


LEGACY_MODES = {"worker", "steward", "auto"}
ROUTER_MODES = {"router", "intelligent"}


@dataclass
class RouteDecision:
    """路由阶段产出的标准决策结构。"""

    target_agents: list[str]
    reason: str
    confidence: float
    plan_required: bool
    strategy: str


@dataclass
class SkillProfile:
    """技能元数据抽象，用于候选筛选与提示词构建。"""

    name: str
    description: str
    content_hint: str
    full_content: str
    skill_dir: str


@dataclass
class SkillDecision:
    """单个子任务的技能选择结果与依据。"""

    selected_skills: list[str]
    source: str
    reason: str
    candidates: list[dict[str, Any]]
    hint_used: list[str]
    hint_invalid: list[str]
