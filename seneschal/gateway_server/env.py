from __future__ import annotations

from pathlib import Path
from typing import Any

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_ENV_SETTINGS_SCHEMA: list[dict[str, Any]] = [
    {
        "id": "runtime",
        "title": "Runtime",
        "items": [
            {"key": "SENESCHAL_LOG_LEVEL", "label": "日志级别", "type": "select", "options": ["DEBUG", "INFO", "WARNING", "ERROR"]},
            {"key": "SENESCHAL_FILE_WRITE_ROOT", "label": "文件输出根目录", "type": "text"},
            {"key": "SENESCHAL_CHAT_SESSION_ROOT", "label": "Chat 会话目录", "type": "text"},
        ],
    },
    {
        "id": "gateway",
        "title": "Gateway",
        "items": [
            {"key": "SENESCHAL_GATEWAY_HOST", "label": "监听主机", "type": "text"},
            {"key": "SENESCHAL_GATEWAY_PORT", "label": "监听端口", "type": "number"},
            {"key": "SENESCHAL_GATEWAY_API_KEY", "label": "API Key", "type": "password"},
            {"key": "SENESCHAL_GATEWAY_PUBLIC_BASE_URL", "label": "公网访问地址", "type": "text"},
            {"key": "SENESCHAL_GATEWAY_FILE_ROOT", "label": "可下载文件根目录", "type": "text"},
            {"key": "SENESCHAL_GATEWAY_CALLBACK_TIMEOUT", "label": "回调超时(s)", "type": "number"},
            {"key": "SENESCHAL_GATEWAY_CALLBACK_RETRY", "label": "回调重试次数", "type": "number"},
            {"key": "SENESCHAL_GATEWAY_CALLBACK_BACKOFF", "label": "回调退避(s)", "type": "number"},
        ],
    },
    {
        "id": "llm",
        "title": "LLM Provider",
        "items": [
            {"key": "OPENROUTER_API_KEY", "label": "OpenRouter API Key", "type": "password"},
            {"key": "OPENROUTER_BASE_URL", "label": "OpenRouter Base URL", "type": "text"},
            {"key": "OPENROUTER_MODEL", "label": "OpenRouter Model", "type": "text"},
            {
                "key": "OPENROUTER_MODEL_FOR_ORCHESTRATOR",
                "label": "OpenRouter Model (Router/Planner/Selector)",
                "type": "text",
            },
        ],
    },
    {
        "id": "brave",
        "title": "Brave Search",
        "items": [
            {"key": "BRAVE_API_KEY", "label": "Brave API Key", "type": "password"},
            {"key": "BRAVE_SEARCH_BASE_URL", "label": "Brave Search Base URL", "type": "text"},
            {"key": "BRAVE_SEARCH_MAX_RESULTS", "label": "最大结果数", "type": "number"},
        ],
    },
    {
        "id": "mobile_executor",
        "title": "Mobile Executor",
        "items": [
            {"key": "MOBILE_PROVIDER", "label": "Provider", "type": "select", "options": ["mobiagent", "uitars", "qwen", "autoglm"]},
            {"key": "MOBILE_OUTPUT_DIR", "label": "输出目录", "type": "text"},
            {"key": "MOBILE_DEVICE_TYPE", "label": "设备平台", "type": "select", "options": ["mock", "Android", "Harmony"]},
            {"key": "MOBILE_DEVICE_ID", "label": "设备 ID/地址", "type": "text"},
            {"key": "MOBILE_API_BASE", "label": "通用 API Base", "type": "text"},
            {"key": "MOBILE_API_KEY", "label": "通用 API Key", "type": "password"},
            {"key": "MOBILE_MODEL", "label": "通用 Model", "type": "text"},
            {"key": "MOBILE_TEMPERATURE", "label": "温度", "type": "number"},
            {"key": "MOBILE_MAX_STEPS", "label": "最大步数", "type": "number"},
            {"key": "MOBILE_DRAW", "label": "绘图调试(0/1)", "type": "text"},
            {"key": "MOBILE_MOBIAGENT_ENABLE_PLANNING", "label": "MobiAgent: Enable Planning", "type": "text"},
            {"key": "MOBILE_MOBIAGENT_USE_E2E", "label": "MobiAgent: Use E2E", "type": "text"},
            {"key": "MOBILE_MOBIAGENT_DECIDER_MODEL", "label": "MobiAgent: Decider Model", "type": "text"},
            {"key": "MOBILE_MOBIAGENT_GROUNDER_MODEL", "label": "MobiAgent: Grounder Model", "type": "text"},
            {"key": "MOBILE_MOBIAGENT_PLANNER_MODEL", "label": "MobiAgent: Planner Model", "type": "text"},
            {"key": "MOBILE_UITARS_STEP_DELAY", "label": "UITARS: Step Delay", "type": "number"},
            {"key": "MOBILE_AUTOGLM_MAX_TOKENS", "label": "AutoGLM: Max Tokens", "type": "number"},
            {"key": "MOBILE_AUTOGLM_TOP_P", "label": "AutoGLM: Top P", "type": "number"},
            {"key": "MOBILE_AUTOGLM_FREQUENCY_PENALTY", "label": "AutoGLM: Frequency Penalty", "type": "number"},
            {"key": "MOBI_AGENT_BASE_URL", "label": "Legacy MobiAgent Base URL", "type": "text"},
            {"key": "MOBI_AGENT_API_KEY", "label": "Legacy MobiAgent API Key", "type": "password"},
        ],
    },
    {
        "id": "routing",
        "title": "Routing",
        "items": [
            {"key": "SENESCHAL_ROUTING_DEFAULT_MODE", "label": "默认模式", "type": "select", "options": ["chat", "router", "intelligent", "worker", "steward", "auto"]},
            {"key": "SENESCHAL_ROUTING_STRATEGY", "label": "路由策略", "type": "text"},
            {"key": "SENESCHAL_ALLOW_LEGACY_MODE", "label": "允许 legacy 模式(0/1)", "type": "text"},
            {"key": "SENESCHAL_ROUTING_MAX_SUBTASKS", "label": "最大子任务数", "type": "number"},
            {"key": "SENESCHAL_ROUTING_MAX_DEPTH", "label": "最大深度", "type": "number"},
            {"key": "SENESCHAL_ROUTER_TIMEOUT_S", "label": "Router 超时(s)", "type": "number"},
            {"key": "SENESCHAL_PLANNER_TIMEOUT_S", "label": "Planner 超时(s)", "type": "number"},
            {"key": "SENESCHAL_SUBTASK_TIMEOUT_S", "label": "子任务超时(s)", "type": "number"},
            {"key": "SENESCHAL_SKILL_SELECTOR_TIMEOUT_S", "label": "Skill Selector 超时(s)", "type": "number"},
        ],
    },
    {
        "id": "feishu",
        "title": "Feishu",
        "items": [
            {"key": "FEISHU_EVENT_TRANSPORT", "label": "事件接入模式", "type": "select", "options": ["both", "webhook", "long_conn", "off", "auto"]},
            {"key": "FEISHU_APP_ID", "label": "App ID", "type": "text"},
            {"key": "FEISHU_APP_SECRET", "label": "App Secret", "type": "password"},
            {"key": "FEISHU_VERIFICATION_TOKEN", "label": "Verification Token", "type": "text"},
            {"key": "FEISHU_ENCRYPT_KEY", "label": "Encrypt Key", "type": "text"},
        ],
    },
]


def _gateway_override(name: str, default: Any) -> Any:
    try:
        from .. import gateway_server
    except Exception:
        return default
    return getattr(gateway_server, name, default)


def _env_file_path() -> Path:
    """返回项目根目录 `.env` 文件路径。"""
    return _ENV_FILE


def _read_env_content() -> str:
    """读取 `.env` 文件原始内容。"""
    env_path = _gateway_override("_env_file_path", _env_file_path)()
    if not env_path.exists():
        return ""
    return env_path.read_text(encoding="utf-8")


def _parse_env_variables(content: str) -> dict[str, str]:
    """从 `.env` 文本解析键值对。"""
    variables: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        ):
            value = value[1:-1]
        variables[key] = value
    return variables


def _write_env_content(content: str) -> None:
    """覆盖写入 `.env` 文件。"""
    env_path = _gateway_override("_env_file_path", _env_file_path)()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(content, encoding="utf-8")


def _managed_env_keys() -> list[str]:
    """返回结构化设置管理的环境变量键列表（按 schema 顺序）。"""
    keys: list[str] = []
    for category in _ENV_SETTINGS_SCHEMA:
        for item in category.get("items", []):
            key = str(item.get("key") or "").strip()
            if key:
                keys.append(key)
    return keys


def _split_env_variables(variables: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """按 schema 拆分受管变量与未纳入 schema 的变量。"""
    managed_key_set = set(_managed_env_keys())
    managed: dict[str, str] = {}
    unmanaged: dict[str, str] = {}
    for key, value in variables.items():
        if key in managed_key_set:
            managed[key] = value
        else:
            unmanaged[key] = value
    return managed, unmanaged


def _sanitize_structured_values(values: dict[str, Any] | None) -> dict[str, str]:
    """清洗结构化表单提交值。"""
    if not isinstance(values, dict):
        return {}
    sanitized: dict[str, str] = {}
    for key, value in values.items():
        key_text = str(key or "").strip()
        if not key_text:
            continue
        value_text = str(value) if value is not None else ""
        sanitized[key_text] = value_text.strip()
    return sanitized


def _format_env_value(value: str) -> str:
    """格式化 `.env` 赋值为 `\"...\"` 双引号形式。"""
    text = str(value or "")
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_structured_env_content(values: dict[str, str], unmanaged: dict[str, str]) -> str:
    """按分类 schema 渲染 `.env` 文本。"""
    lines: list[str] = [
        "# Auto-generated by Seneschal Gateway Console",
        "# Edit via /console settings page or update manually if needed.",
        "",
    ]

    for category in _ENV_SETTINGS_SCHEMA:
        title = str(category.get("title") or "Settings")
        lines.append(f"# ===== {title} =====")
        for item in category.get("items", []):
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            value = values.get(key, "")
            formatted = _format_env_value(value)
            lines.append(f"export {key}={formatted}")
        lines.append("")

    if unmanaged:
        lines.append("# ===== Unmanaged Variables =====")
        for key in sorted(unmanaged.keys()):
            formatted = _format_env_value(unmanaged[key])
            lines.append(f"export {key}={formatted}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
