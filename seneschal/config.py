# -*- coding: utf-8 -*-
"""Seneschal 配置模块。"""

from __future__ import annotations

import os

# LLM 模型配置 - 优先从环境变量读取，否则使用默认值
MODEL_CONFIG = {
    "model_name": os.environ.get("OPENROUTER_MODEL", os.environ.get("OPENAI_MODEL", "google/gemini-3-flash-preview")),
    "api_key": os.environ.get("OPENROUTER_API_KEY", os.environ.get("OPENAI_API_KEY", "sk-or-v1-xxx")),
    "api_base": os.environ.get("OPENROUTER_BASE_URL", os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")),
    "temperature": 0.5,
}

# MobiAgent 配置 (端侧执行 Agent 的 API 地址)
MOBI_AGENT_CONFIG = {
    "base_url": os.environ.get("MOBI_AGENT_BASE_URL", "http://localhost:8080"),
    "api_key": os.environ.get("MOBI_AGENT_API_KEY", "mobi-xxx"),
}

# WeKnora 配置 (知识库 API 地址) — legacy, used by dailytasks runner
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

# RAG 配置 (本地向量知识库)
RAG_CONFIG = {
    "store_path": os.environ.get("SENESCHAL_RAG_STORE_PATH", "~/.seneschal/rag_store"),
    "collection_name": os.environ.get("SENESCHAL_RAG_COLLECTION", "seneschal_tasks"),
    "embedding_model": os.environ.get("SENESCHAL_RAG_EMBEDDING_MODEL", "openai/text-embedding-3-small"),
    "embedding_dimensions": int(os.environ.get("SENESCHAL_RAG_EMBEDDING_DIMENSIONS", "1536")),
    "chunk_size": int(os.environ.get("SENESCHAL_RAG_CHUNK_SIZE", "512")),
    "index_file_content": os.environ.get("SENESCHAL_RAG_INDEX_FILE_CONTENT", "0").strip() not in {"0", "false", "False"},
    "task_history_enabled": os.environ.get("SENESCHAL_RAG_TASK_HISTORY", "0").strip() not in {"0", "false", "False"},
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
    "upstream_context_max_chars": max(200, int(os.environ.get("SENESCHAL_UPSTREAM_CONTEXT_MAX_CHARS", "4000"))),
    "upstream_context_max_steps": max(1, int(os.environ.get("SENESCHAL_UPSTREAM_CONTEXT_MAX_STEPS", "20"))),
    "skill_enabled": os.environ.get("SENESCHAL_SKILL_ENABLED", "1").strip() not in {"0", "false", "False"},
    "skill_root_dir": os.environ.get("SENESCHAL_SKILL_ROOT_DIR", ""),
    "skill_max_per_subtask": max(0, int(os.environ.get("SENESCHAL_SKILL_MAX_PER_SUBTASK", "2"))),
    "skill_selector_timeout_s": max(0.5, float(os.environ.get("SENESCHAL_SKILL_SELECTOR_TIMEOUT_S", "20"))),
    "skill_llm_rerank": os.environ.get("SENESCHAL_SKILL_LLM_RERANK", "1").strip() not in {"0", "false", "False"},
    "skill_rule_max_candidates": max(1, int(os.environ.get("SENESCHAL_SKILL_RULE_MAX_CANDIDATES", "8"))),
    "skill_hint_override": os.environ.get("SENESCHAL_SKILL_HINT_OVERRIDE", "1").strip() not in {"0", "false", "False"},
}

# 长期记忆配置
MEMORY_CONFIG = {
    "enabled": os.environ.get("SENESCHAL_MEMORY_ENABLED", "0").strip() not in {"0", "false", "False"},
    "file_path": os.environ.get("SENESCHAL_MEMORY_FILE", "~/.seneschal/MEMORY.md"),
}
