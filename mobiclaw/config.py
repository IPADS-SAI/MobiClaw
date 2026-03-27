# -*- coding: utf-8 -*-
"""MobiClaw 配置模块。"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from .env import load_project_env
from .mobile.config import resolve_device_config, resolve_provider_config


logger = logging.getLogger(__name__)


load_project_env()


def _custom_agent_config_path() -> Path:
    raw = (os.environ.get("MOBICLAW_CUSTOM_AGENT_CONFIG_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parent / "configs" / "custom_agent.json"


def _load_custom_agents() -> list[dict[str, object]]:
    path = _custom_agent_config_path()
    if not path.exists() or not path.is_file():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("custom agent config parse failed: path=%s error=%s", path, exc)
        return []

    if isinstance(payload, dict):
        payload = payload.get("agents", [])

    if not isinstance(payload, list):
        logger.warning("custom agent config must be list or {\"agents\": [...]}: path=%s", path)
        return []

    agents: list[dict[str, object]] = []
    for item in payload:
        if isinstance(item, dict):
            agents.append(item)
        else:
            logger.warning("custom agent config item ignored (not object): path=%s item=%r", path, item)
    return agents

# LLM 模型配置 - 优先从环境变量读取，否则使用默认值
MODEL_CONFIG = {
    "model_name": os.environ.get("OPENROUTER_MODEL", os.environ.get("OPENAI_MODEL", "google/gemini-3-flash-preview")),
    "orchestrator_model_name": os.environ.get(
        "OPENROUTER_MODEL_FOR_ORCHESTRATOR",
        os.environ.get("OPENROUTER_MODEL", os.environ.get("OPENAI_MODEL", "google/gemini-3-flash-preview")),
    ),
    "api_key": os.environ.get("OPENROUTER_API_KEY", os.environ.get("OPENAI_API_KEY", "sk-or-v1-xxx")),
    "api_base": os.environ.get("OPENROUTER_BASE_URL", os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")),
    "temperature": 0.5,
}

# MobiAgent 配置 (端侧执行 Agent 的 API 地址)
MOBI_AGENT_CONFIG = {
    "base_url": os.environ.get("MOBI_AGENT_BASE_URL", "http://localhost:8080"),
    "api_key": os.environ.get("MOBI_AGENT_API_KEY", "mobi-xxx"),
}

# Mobile Executor 配置 (本地统一手机任务执行器)
_mobile_provider_cfg = resolve_provider_config(provider=None)
_mobile_device_type, _mobile_device_id = resolve_device_config()
MOBILE_EXECUTOR_CONFIG = {
    "provider": _mobile_provider_cfg.name,
    "output_dir": os.environ.get("MOBILE_OUTPUT_DIR", "outputs/mobile_exec"),
    "device_type": _mobile_device_type,
    "device_id": _mobile_device_id,
    "api_base": _mobile_provider_cfg.api_base,
    "api_key": _mobile_provider_cfg.api_key,
    "model": _mobile_provider_cfg.model,
    "temperature": _mobile_provider_cfg.temperature,
    "max_steps": _mobile_provider_cfg.max_steps,
    "draw": _mobile_provider_cfg.draw,
    "extras": dict(_mobile_provider_cfg.extras),
}

MOBILE_TASK_BACKEND_CONFIG = {
    "mode": (os.environ.get("MOBILE_EXECUTION_MODE", "local").strip().lower() or "local"),
    "poll_interval_s": max(0.2, float(os.environ.get("MOBILE_REMOTE_POLL_INTERVAL_S", "2.0"))),
    "timeout_s": max(5.0, float(os.environ.get("MOBILE_REMOTE_TIMEOUT_S", "900"))),
}

# RAG 配置 (本地向量知识库)
RAG_CONFIG = {
    "store_path": os.environ.get("MOBICLAW_RAG_STORE_PATH", "~/.mobiclaw/rag_store"),
    "collection_name": os.environ.get("MOBICLAW_RAG_COLLECTION", "mobiclaw_tasks"),
    "embedding_model": os.environ.get("MOBICLAW_RAG_EMBEDDING_MODEL", "openai/text-embedding-3-small"),
    "embedding_dimensions": int(os.environ.get("MOBICLAW_RAG_EMBEDDING_DIMENSIONS", "1536")),
    "chunk_size": int(os.environ.get("MOBICLAW_RAG_CHUNK_SIZE", "512")),
    "index_file_content": os.environ.get("MOBICLAW_RAG_INDEX_FILE_CONTENT", "0").strip() not in {"0", "false", "False"},
    "task_history_enabled": os.environ.get("MOBICLAW_RAG_STORE_HISTORY", "1").strip() not in {"0", "false", "False"},
}

# Brave Search 配置 (联网搜索)
BRAVE_SEARCH_CONFIG = {
    "api_key": os.environ.get("BRAVE_API_KEY", ""),
    "base_url": os.environ.get("BRAVE_SEARCH_BASE_URL", "https://api.search.brave.com/res/v1/web/search"),
    "max_results": int(os.environ.get("BRAVE_SEARCH_MAX_RESULTS", "5")),
}

# Multi-agent routing configuration
ROUTING_CONFIG = {
    "default_mode": os.environ.get("MOBICLAW_ROUTING_DEFAULT_MODE", "router").strip().lower() or "router",
    "strategy": os.environ.get("MOBICLAW_ROUTING_STRATEGY", "llm_rule_hybrid").strip().lower() or "llm_rule_hybrid",
    "allow_legacy_mode": os.environ.get("MOBICLAW_ALLOW_LEGACY_MODE", "1").strip() not in {"0", "false", "False"},
    "max_subtasks": max(1, int(os.environ.get("MOBICLAW_ROUTING_MAX_SUBTASKS", "4"))),
    "max_routing_depth": max(1, int(os.environ.get("MOBICLAW_ROUTING_MAX_DEPTH", "2"))),
    "router_timeout_s": max(1.0, float(os.environ.get("MOBICLAW_ROUTER_TIMEOUT_S", "60"))),
    "planner_timeout_s": max(1.0, float(os.environ.get("MOBICLAW_PLANNER_TIMEOUT_S", "60"))),
    "subtask_timeout_s": max(1.0, float(os.environ.get("MOBICLAW_SUBTASK_TIMEOUT_S", "600"))),
    "upstream_context_max_chars": max(200, int(os.environ.get("MOBICLAW_UPSTREAM_CONTEXT_MAX_CHARS", "4000"))),
    "upstream_context_max_steps": max(1, int(os.environ.get("MOBICLAW_UPSTREAM_CONTEXT_MAX_STEPS", "20"))),
    "skill_enabled": os.environ.get("MOBICLAW_SKILL_ENABLED", "1").strip() not in {"0", "false", "False"},
    "skill_root_dir": os.environ.get("MOBICLAW_SKILL_ROOT_DIR", ""),
    "skill_max_per_subtask": max(0, int(os.environ.get("MOBICLAW_SKILL_MAX_PER_SUBTASK", "2"))),
    "skill_selector_timeout_s": max(0.5, float(os.environ.get("MOBICLAW_SKILL_SELECTOR_TIMEOUT_S", "20"))),
    "skill_llm_rerank": os.environ.get("MOBICLAW_SKILL_LLM_RERANK", "1").strip() not in {"0", "false", "False"},
    "skill_rule_max_candidates": max(1, int(os.environ.get("MOBICLAW_SKILL_RULE_MAX_CANDIDATES", "8"))),
    "skill_hint_override": os.environ.get("MOBICLAW_SKILL_HINT_OVERRIDE", "1").strip() not in {"0", "false", "False"},
}

# 定时任务调度配置
SCHEDULE_CONFIG = {
    "enabled": os.environ.get("MOBICLAW_SCHEDULE_ENABLED", "1").strip() not in {"0", "false", "False"},
    "store_path": os.environ.get("MOBICLAW_SCHEDULE_STORE_PATH", "~/.mobiclaw/schedules.json")
}

# 长期记忆配置
MEMORY_CONFIG = {
    "enabled": os.environ.get("MOBICLAW_MEMORY_ENABLED", "1").strip() not in {"0", "false", "False"},
    "file_path": os.environ.get("MOBICLAW_MEMORY_FILE", "~/.mobiclaw/MEMORY.md"),
}

# 工具超时配置 (所有 Agent 工具的默认超时秒数)
TOOL_CONFIG = {
    "worker_timeout_s": max(5.0, float(os.environ.get("MOBICLAW_WORKER_TOOL_TIMEOUT_S", "120"))),
    "steward_timeout_s": max(5.0, float(os.environ.get("MOBICLAW_STEWARD_TOOL_TIMEOUT_S", "300"))),
    "custom_timeout_s": max(5.0, float(os.environ.get("MOBICLAW_CUSTOM_TOOL_TIMEOUT_S", "120"))),
    "chat_timeout_s": max(5.0, float(os.environ.get("MOBICLAW_CHAT_TOOL_TIMEOUT_S", "120"))),
}

# Office 文件生成/修改工具开关（默认关闭）
CREATE_OFFICE_FILE_CONFIG = {
    "enabled": os.environ.get("MOBICLAW_CREATE_OFFICE_FILE_ENABLED", "0").strip() not in {"0", "false", "False"},
}

# MCP 服务器动态工具注册
MCP_SERVERS_CONFIG = {
    "config_path": os.environ.get("MOBICLAW_MCP_SERVERS_PATH", "~/.mobiclaw/mcp_servers.json"),
}

# 自定义 Agent 配置（配置驱动自动注册）
CUSTOM_AGENT_CONFIG = {
    "path": str(_custom_agent_config_path()),
    "agents": _load_custom_agents(),
}
