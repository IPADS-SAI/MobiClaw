# -*- coding: utf-8 -*-
"""Seneschal 配置模块。"""

from __future__ import annotations

import os

# LLM 模型配置 - 优先从环境变量读取，否则使用默认值
MODEL_CONFIG = {
    "model_name": os.environ.get("OPENROUTER_MODEL", os.environ.get("OPENAI_MODEL", "google/gemini-2.5-flash")),
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
