# -*- coding: utf-8 -*-
"""MobiClaw Agent 构建与能力注册模块。

该文件作为兼容导出层，保留历史导入路径：
- from mobiclaw.agents import create_worker_agent, ...
- from mobiclaw.agents import AgentCapability, ...
"""

from __future__ import annotations

from .catalog import _builtin_agent_capabilities, _tool_catalog
from .common import _extract_text_from_model_response, create_openai_model
from .custom import (
    _load_custom_agent_definitions,
    create_configured_agent_by_name,
    get_agent_capability_descriptions,
)
from .factories import (
    create_chat_agent,
    create_planner_agent,
    create_router_agent,
    create_skill_selector_agent,
    create_steward_agent,
    create_user_agent,
    create_worker_agent,
)
from .types import AgentCapability, CustomAgentDefinition, _as_str_list, _normalize_agent_name

__all__ = [
    "AgentCapability",
    "CustomAgentDefinition",
    "create_chat_agent",
    "create_configured_agent_by_name",
    "create_planner_agent",
    "create_router_agent",
    "create_skill_selector_agent",
    "create_steward_agent",
    "create_user_agent",
    "create_worker_agent",
    "get_agent_capability_descriptions",
]
