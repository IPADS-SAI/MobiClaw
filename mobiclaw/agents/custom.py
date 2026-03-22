# -*- coding: utf-8 -*-
"""mobiclaw.agents 的自定义 agent 装载与能力聚合。"""

from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
import logging

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit

from ..config import CUSTOM_AGENT_CONFIG, MEMORY_CONFIG, RAG_CONFIG, TOOL_CONFIG
from .catalog import _builtin_agent_capabilities, _tool_catalog
from .common import _build_memory_prompt, _build_skill_prompt_suffix, create_openai_model, register_tool_with_timeout
from .types import AgentCapability, CustomAgentDefinition, _as_str_list, _normalize_agent_name

logger = logging.getLogger("mobiclaw.agents")


@lru_cache(maxsize=1)
def _load_custom_agent_definitions() -> tuple[CustomAgentDefinition, ...]:
    raw_items = CUSTOM_AGENT_CONFIG.get("agents", []) if isinstance(CUSTOM_AGENT_CONFIG, dict) else []
    if not isinstance(raw_items, list) or not raw_items:
        return ()

    catalog = _tool_catalog()
    reserved = {"worker", "steward", "router", "planner", "skillselector", "user", "mobichatbot"}
    seen: set[str] = set()
    defs: list[CustomAgentDefinition] = []

    for idx, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            logger.warning("custom agent ignored: index=%d reason=not_object", idx)
            continue

        display_name = str(raw.get("agent_name") or "").strip()
        role = str(raw.get("role") or "").strip()
        system_prompt = str(raw.get("system_prompt") or "").strip()
        if not display_name or not role or not system_prompt:
            logger.warning("custom agent ignored: index=%d reason=missing_required_fields", idx)
            continue

        name = _normalize_agent_name(display_name)
        if not name:
            logger.warning("custom agent ignored: index=%d reason=invalid_name", idx)
            continue
        if name in reserved:
            logger.warning("custom agent ignored: name=%s reason=reserved_name", name)
            continue
        if name in seen:
            logger.warning("custom agent ignored: name=%s reason=duplicate", name)
            continue

        raw_tools = _as_str_list(raw.get("tools"))
        tools = list(dict.fromkeys([_normalize_agent_name(tool) for tool in raw_tools if _normalize_agent_name(tool)]))
        unknown_tools = [tool for tool in tools if tool not in catalog]
        if unknown_tools:
            logger.warning(
                "custom agent ignored: name=%s reason=unknown_tools unknown=%s",
                name,
                unknown_tools,
            )
            continue

        temperature: float | None = None
        if raw.get("temperature") is not None:
            try:
                temperature = float(raw.get("temperature"))
            except (TypeError, ValueError):
                logger.warning("custom agent ignored: name=%s reason=invalid_temperature", name)
                continue

        max_iters = 12
        if raw.get("max_iters") is not None:
            try:
                max_iters = max(1, min(50, int(raw.get("max_iters"))))
            except (TypeError, ValueError):
                logger.warning("custom agent ignored: name=%s reason=invalid_max_iters", name)
                continue

        model_name = str(raw.get("model_name") or "").strip() or None
        defs.append(
            CustomAgentDefinition(
                name=name,
                display_name=display_name,
                role=role,
                system_prompt=system_prompt,
                tools=tools,
                strengths=_as_str_list(raw.get("strengths")),
                typical_tasks=_as_str_list(raw.get("typical_tasks")),
                boundaries=_as_str_list(raw.get("boundaries")),
                model_name=model_name,
                temperature=temperature,
                max_iters=max_iters,
            )
        )
        seen.add(name)

    return tuple(defs)


def get_agent_capability_descriptions() -> dict[str, dict[str, object]]:
    """返回路由可用的 Agent 能力描述字典。"""
    registry = _builtin_agent_capabilities()
    for item in _load_custom_agent_definitions():
        registry.append(
            AgentCapability(
                name=item.name,
                role=item.role,
                strengths=item.strengths,
                typical_tasks=item.typical_tasks,
                boundaries=item.boundaries,
            )
        )
    return {item.name: asdict(item) for item in registry}


def create_configured_agent_by_name(agent_name: str, *, skill_context: str | None = None) -> ReActAgent | None:
    """根据 custom_agent.json 定义按名称构建自定义 Agent。"""
    normalized = _normalize_agent_name(agent_name)
    target = None
    for item in _load_custom_agent_definitions():
        if item.name == normalized:
            target = item
            break
    if target is None:
        return None

    toolkit = Toolkit()
    tool_timeout_s = TOOL_CONFIG["timeout_s"]
    catalog = _tool_catalog()
    for tool_name in target.tools:
        func, desc = catalog[tool_name]
        if tool_name == "search_task_history" and not RAG_CONFIG["task_history_enabled"]:
            continue
        if tool_name == "update_long_term_memory" and not MEMORY_CONFIG["enabled"]:
            continue
        register_tool_with_timeout(toolkit, tool_timeout_s, func, func_description=desc)

    sys_prompt = target.system_prompt
    sys_prompt += _build_memory_prompt()
    sys_prompt += _build_skill_prompt_suffix(skill_context)

    return ReActAgent(
        name=target.display_name,
        sys_prompt=sys_prompt,
        model=create_openai_model(
            temperature=target.temperature,
            model_name=target.model_name,
        ),
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        max_iters=target.max_iters,
    )
