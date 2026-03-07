# -*- coding: utf-8 -*-
"""Seneschal 配置模块。"""

from __future__ import annotations

import os

# LLM 模型配置 - 优先从环境变量读取，否则使用默认值
MODEL_CONFIG = {
    "model_name": os.environ.get("OPENROUTER_MODEL", os.environ.get("OPENAI_MODEL", "qwen/qwen3.5-397b-a17b")),
    "api_key": os.environ.get("OPENROUTER_API_KEY", os.environ.get("OPENAI_API_KEY", "sk-or-v1-xxx")),
    "api_base": os.environ.get("OPENROUTER_BASE_URL", os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")),
    "temperature": 0.5,
}

# MobiAgent 配置 (端侧执行 Agent 的 API 地址)
MOBI_AGENT_CONFIG = {
    "base_url": os.environ.get("MOBI_AGENT_BASE_URL", "http://localhost:8080"),
    "api_key": os.environ.get("MOBI_AGENT_API_KEY", "mobi-xxx"),
}

# WeKnora 配置 (知识库 API 地址)
WEKNORA_CONFIG = {
    "base_url": os.environ.get("WEKNORA_BASE_URL", "http://localhost:8080"),
    "api_key": os.environ.get(
        "WEKNORA_API_KEY",
        "sk-Q-xxx",
    ),
    "knowledge_base_name": os.environ.get("WEKNORA_KB_NAME", ""),
    "agent_name": os.environ.get("WEKNORA_AGENT_NAME", ""),
    "session_id": os.environ.get("WEKNORA_SESSION_ID", "seneschal-session"),
}

# Brave Search 配置 (联网搜索)
BRAVE_SEARCH_CONFIG = {
    "api_key": os.environ.get("BRAVE_API_KEY", ""),
    "base_url": os.environ.get("BRAVE_SEARCH_BASE_URL", "https://api.search.brave.com/res/v1/web/search"),
    "max_results": int(os.environ.get("BRAVE_SEARCH_MAX_RESULTS", "5")),
}

# Multi-agent routing configuration
ROUTING_CONFIG = {
    "default_mode": os.environ.get("SENESCHAL_ROUTING_DEFAULT_MODE", "router").strip().lower() or "router",
    "strategy": os.environ.get("SENESCHAL_ROUTING_STRATEGY", "llm_rule_hybrid").strip().lower() or "llm_rule_hybrid",
    "allow_legacy_mode": os.environ.get("SENESCHAL_ALLOW_LEGACY_MODE", "1").strip() not in {"0", "false", "False"},
    "max_subtasks": max(1, int(os.environ.get("SENESCHAL_ROUTING_MAX_SUBTASKS", "4"))),
    "max_routing_depth": max(1, int(os.environ.get("SENESCHAL_ROUTING_MAX_DEPTH", "2"))),
    "router_timeout_s": max(1.0, float(os.environ.get("SENESCHAL_ROUTER_TIMEOUT_S", "60"))),
    "planner_timeout_s": max(1.0, float(os.environ.get("SENESCHAL_PLANNER_TIMEOUT_S", "60"))),
    "subtask_timeout_s": max(5.0, float(os.environ.get("SENESCHAL_SUBTASK_TIMEOUT_S", "300"))),
}
