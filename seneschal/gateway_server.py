# -*- coding: utf-8 -*-
"""Seneschal 对外任务网关服务。

核心功能：
- 提供任务提交、异步任务查询、结果文件下载接口；
- 支持 webhook 回调与飞书消息回传；
- 支持飞书 webhook 与长连接两种事件接入方式。
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
from contextlib import asynccontextmanager
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
import requests

from .env import load_project_env

logger = logging.getLogger(__name__)

load_project_env()

from .workflows import run_gateway_task


def _configure_logging() -> None:
    """Ensure gateway and orchestrator logs are visible under module startup."""
    level_name = (os.environ.get("SENESCHAL_LOG_LEVEL", "INFO") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s : %(message)s",
        )
    else:
        # Keep existing handlers from uvicorn, only raise/lower threshold.
        root_logger.setLevel(level)

    logging.getLogger("seneschal").setLevel(level)


_configure_logging()


@dataclass
class GatewayConfig:
    """网关运行配置。"""

    api_key: str
    callback_timeout_s: float
    callback_retry: int
    callback_retry_backoff_s: float
    public_base_url: str | None
    file_root: str | None
    feishu_app_id: str
    feishu_app_secret: str
    feishu_verification_token: str
    feishu_encrypt_key: str
    feishu_event_transport: str


def load_config() -> GatewayConfig:
    """从环境变量读取网关配置并构建配置对象。"""
    return GatewayConfig(
        api_key=os.environ.get("SENESCHAL_GATEWAY_API_KEY", ""),
        callback_timeout_s=float(os.environ.get("SENESCHAL_GATEWAY_CALLBACK_TIMEOUT", "10")),
        callback_retry=max(1, int(os.environ.get("SENESCHAL_GATEWAY_CALLBACK_RETRY", "3"))),
        callback_retry_backoff_s=float(os.environ.get("SENESCHAL_GATEWAY_CALLBACK_BACKOFF", "1.0")),
        public_base_url=(os.environ.get("SENESCHAL_GATEWAY_PUBLIC_BASE_URL") or "").strip() or None,
        file_root=(os.environ.get("SENESCHAL_GATEWAY_FILE_ROOT") or "").strip() or None,
        feishu_app_id=os.environ.get("FEISHU_APP_ID", "").strip(),
        feishu_app_secret=os.environ.get("FEISHU_APP_SECRET", "").strip(),
        feishu_verification_token=os.environ.get("FEISHU_VERIFICATION_TOKEN", "").strip(),
        feishu_encrypt_key=os.environ.get("FEISHU_ENCRYPT_KEY", "").strip(),
        feishu_event_transport=os.environ.get("FEISHU_EVENT_TRANSPORT", "both").strip().lower() or "both",
    )


class TaskRequest(BaseModel):
    """任务提交请求体。"""

    task: str
    async_mode: bool = Field(default=False)
    output_path: str | None = None
    mode: str = Field(default="router")
    agent_hint: str | None = None
    skill_hint: str | None = None
    routing_strategy: str | None = None
    context_id: str | None = None
    webhook_url: str | None = None
    webhook_token: str | None = None
    callback_headers: dict[str, str] = Field(default_factory=dict)


class TaskResult(BaseModel):
    """任务状态与结果响应体。"""

    job_id: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None


class EnvContentRequest(BaseModel):
    """`.env` 文件更新请求体。"""

    content: str = Field(default="")


class EnvStructuredRequest(BaseModel):
    """结构化 `.env` 配置更新请求体。"""

    values: dict[str, str] = Field(default_factory=dict)
    unmanaged: dict[str, str] | None = None
    preserve_unmanaged: bool = Field(default=True)


@dataclass
class JobContext:
    """异步任务上下文（回调地址与飞书投递信息）。"""

    webhook_url: str | None = None
    webhook_token: str | None = None
    callback_headers: dict[str, str] | None = None
    feishu_chat_id: str | None = None
    feishu_user_open_id: str | None = None
    feishu_message_id: str | None = None
    feishu_receive_id_type: str = "chat_id"


_JOB_STORE: dict[str, TaskResult] = {}
_JOB_CONTEXT: dict[str, JobContext] = {}
_JOB_LOCK = asyncio.Lock()
_MAIN_LOOP: asyncio.AbstractEventLoop | None = None
_FEISHU_WS_THREAD: threading.Thread | None = None
_WEBUI_INDEX = Path(__file__).resolve().parent / "webui" / "gateway_console.html"
_WEBUI_SETTINGS = Path(__file__).resolve().parent / "webui" / "gateway_settings.html"
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
_ENV_SETTINGS_SCHEMA: list[dict[str, Any]] = [
    {
        "id": "runtime",
        "title": "Runtime",
        "items": [
            {"key": "SENESCHAL_LOG_LEVEL", "label": "日志级别", "type": "select", "options": ["DEBUG", "INFO", "WARNING", "ERROR"]},
            {"key": "SENESCHAL_FILE_WRITE_ROOT", "label": "文件输出根目录", "type": "text"},
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
        ],
    },
    {
        "id": "weknora",
        "title": "WeKnora",
        "items": [
            {"key": "WEKNORA_BASE_URL", "label": "WeKnora Base URL", "type": "text"},
            {"key": "WEKNORA_API_KEY", "label": "WeKnora API Key", "type": "password"},
            {"key": "WEKNORA_KB_NAME", "label": "知识库名称", "type": "text"},
            {"key": "WEKNORA_AGENT_NAME", "label": "Agent 名称", "type": "text"},
            {"key": "WEKNORA_SESSION_ID", "label": "Session ID", "type": "text"},
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
        "id": "mobiagent",
        "title": "MobiAgent",
        "items": [
            {"key": "MOBI_AGENT_BASE_URL", "label": "MobiAgent Base URL", "type": "text"},
            {"key": "MOBI_AGENT_API_KEY", "label": "MobiAgent API Key", "type": "password"},
            {"key": "MOBIAGENT_SERVER_MODE", "label": "服务模式", "type": "select", "options": ["cli", "api"]},
            {"key": "MOBIAGENT_SERVER_IP", "label": "服务 IP", "type": "text"},
            {"key": "MOBIAGENT_SERVER_DECIDER_PORT", "label": "Decider 端口", "type": "number"},
            {"key": "MOBIAGENT_SERVER_GROUNDER_PORT", "label": "Grounder 端口", "type": "number"},
            {"key": "MOBIAGENT_SERVER_PLANNER_PORT", "label": "Planner 端口", "type": "number"},
            {"key": "DEVICE", "label": "设备平台", "type": "select", "options": ["Android", "Harmony"]},
            {"key": "MOBIAGENT_CLI_CMD", "label": "CLI 命令模板", "type": "textarea", "raw": True},
            {"key": "MOBIAGENT_TASK_DIR", "label": "任务目录", "type": "text"},
            {"key": "MOBIAGENT_DATA_DIR", "label": "数据目录", "type": "text"},
            {"key": "MOBIAGENT_QUEUE_DIR", "label": "队列目录", "type": "text"},
            {"key": "MOBIAGENT_RESULT_DIR", "label": "结果目录", "type": "text"},
            {"key": "MOBIAGENT_GATEWAY_PORT", "label": "MobiAgent 网关端口", "type": "number"},
        ],
    },
    {
        "id": "routing",
        "title": "Routing",
        "items": [
            {"key": "SENESCHAL_ROUTING_DEFAULT_MODE", "label": "默认模式", "type": "select", "options": ["router", "intelligent", "worker", "steward", "auto"]},
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
    {
        "id": "weknora_models",
        "title": "WeKnora Models",
        "items": [
            {"key": "WEKNORA_MODEL_RERANK_ID", "label": "Rerank ID", "type": "text"},
            {"key": "WEKNORA_MODEL_RERANK_NAME", "label": "Rerank Name", "type": "text"},
            {"key": "WEKNORA_MODEL_RERANK_API_KEY", "label": "Rerank API Key", "type": "password"},
            {"key": "WEKNORA_MODEL_RERANK_BASE_URL", "label": "Rerank Base URL", "type": "text"},
            {"key": "WEKNORA_MODEL_KNOWLEDGE_QA_ID", "label": "KnowledgeQA ID", "type": "text"},
            {"key": "WEKNORA_MODEL_KNOWLEDGE_QA_NAME", "label": "KnowledgeQA Name", "type": "text"},
            {"key": "WEKNORA_MODEL_KNOWLEDGE_QA_ALT_ID", "label": "KnowledgeQA Alt ID", "type": "text"},
            {"key": "WEKNORA_MODEL_KNOWLEDGE_QA_ALT_NAME", "label": "KnowledgeQA Alt Name", "type": "text"},
            {"key": "WEKNORA_MODEL_VLM_ID", "label": "VLM ID", "type": "text"},
            {"key": "WEKNORA_MODEL_VLM_NAME", "label": "VLM Name", "type": "text"},
            {"key": "WEKNORA_MODEL_EMBEDDING_ID", "label": "Embedding ID", "type": "text"},
            {"key": "WEKNORA_MODEL_EMBEDDING_NAME", "label": "Embedding Name", "type": "text"},
            {"key": "WEKNORA_MODEL_EMBEDDING_API_KEY", "label": "Embedding API Key/表达式", "type": "text", "raw": True},
            {"key": "WEKNORA_MODEL_EMBEDDING_BASE_URL", "label": "Embedding Base URL", "type": "text"},
        ],
    },
]


@asynccontextmanager
async def _lifespan(_: FastAPI):
    """应用生命周期：初始化主事件循环并按配置启动飞书长连接。"""
    global _MAIN_LOOP
    _MAIN_LOOP = asyncio.get_running_loop()
    cfg = load_config()

    logger.info("Feishu transport mode: %s", cfg.feishu_event_transport)
    if _should_start_feishu_long_conn(cfg):
        _start_feishu_long_connection(cfg)
    else:
        logger.info("Feishu long connection disabled by FEISHU_EVENT_TRANSPORT")
    yield


app = FastAPI(title="Seneschal Gateway", version="0.1.0", lifespan=_lifespan)


def _ensure_auth(authorization: str | None, cfg: GatewayConfig) -> None:
    """校验 Bearer Token 鉴权信息。"""
    if not cfg.api_key:
        return
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Authorization format")
    token = authorization[len(prefix):]
    if token != cfg.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def _env_file_path() -> Path:
    """返回项目根目录 `.env` 文件路径。"""
    return _ENV_FILE


def _read_env_content() -> str:
    """读取 `.env` 文件原始内容。"""
    env_path = _env_file_path()
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
    env_path = _env_file_path()
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
    return f"\"{escaped}\""


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


def _resolve_file_root(cfg: GatewayConfig) -> Path | None:
    """解析允许暴露下载文件的根目录。"""
    if not cfg.file_root:
        return None
    return Path(cfg.file_root).expanduser().resolve()


def _can_expose_file(path: str, cfg: GatewayConfig) -> bool:
    """判断文件是否允许通过下载接口暴露。"""
    root = _resolve_file_root(cfg)
    target = Path(path).expanduser()
    try:
        resolved = target.resolve()
    except FileNotFoundError:
        return False
    if not resolved.exists() or not resolved.is_file():
        return False
    if root is None:
        return True
    return resolved == root or root in resolved.parents


def _build_download_url(job_id: str, file_name: str, request: Request | None, cfg: GatewayConfig) -> str:
    """构建文件下载 URL。"""
    base = cfg.public_base_url
    if not base and request is not None:
        base = str(request.base_url).rstrip("/")
    if not base:
        return f"/api/v1/files/{job_id}/{file_name}"
    return f"{base}/api/v1/files/{job_id}/{file_name}"


def _decorate_result_with_files(job_id: str, result: dict[str, Any], request: Request | None, cfg: GatewayConfig) -> dict[str, Any]:
    """为结果中的文件条目补充安全过滤后的下载链接。"""
    files = result.get("files") if isinstance(result, dict) else None
    if not isinstance(files, list):
        return result
    enriched: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        name = str(item.get("name") or "").strip()
        if not path or not name:
            continue
        if not _can_expose_file(path, cfg):
            continue
        enriched_item = dict(item)
        enriched_item["download_url"] = _build_download_url(job_id, name, request, cfg)
        enriched.append(enriched_item)
    result = dict(result)
    result["files"] = enriched
    return result


def _parse_feishu_text_from_content(content: str) -> str:
    """解析飞书消息内容并提取文本字段。"""
    parsed = (content or "").strip()
    if not parsed:
        return ""
    try:
        payload = json.loads(parsed)
    except json.JSONDecodeError:
        return parsed
    return str(payload.get("text") or "").strip()


async def _enqueue_feishu_job(
    task: str,
    *,
    chat_id: str | None,
    open_id: str | None,
    message_id: str | None,
) -> str:
    """创建飞书触发的异步任务并入队执行。"""
    job_id = uuid.uuid4().hex
    async with _JOB_LOCK:
        _JOB_STORE[job_id] = TaskResult(job_id=job_id, status="queued")
        _JOB_CONTEXT[job_id] = JobContext(
            feishu_chat_id=chat_id,
            feishu_user_open_id=open_id,
            feishu_message_id=message_id,
            feishu_receive_id_type="chat_id" if chat_id else "open_id",
        )
    asyncio.create_task(
        _run_job(
            job_id,
            task,
            output_path=None,
            mode="router",
            agent_hint=None,
            skill_hint=None,
            routing_strategy=None,
            context_id=message_id or chat_id or open_id,
        )
    )
    return job_id


async def _accept_feishu_message(
    *,
    content: str,
    chat_id: str | None,
    open_id: str | None,
    message_id: str | None,
) -> dict[str, Any]:
    """接收飞书消息并转换为网关任务。"""
    task = _parse_feishu_text_from_content(content)
    if not task:
        return {"ok": True, "accepted": False, "reason": "empty_task"}

    job_id = await _enqueue_feishu_job(
        task,
        chat_id=chat_id,
        open_id=open_id,
        message_id=message_id,
    )
    logger.info("Feishu event accepted, job_id=%s message_id=%s", job_id, message_id or "")
    return {"ok": True, "accepted": True, "job_id": job_id}


def _should_start_feishu_long_conn(cfg: GatewayConfig) -> bool:
    """根据配置判断是否启用飞书长连接监听。"""
    mode = (cfg.feishu_event_transport or "both").strip().lower()
    if mode in {"off", "disabled", "none", "webhook"}:
        return False
    if mode in {"long_conn", "long-connection", "ws", "both"}:
        return True
    if mode == "auto":
        return True
    logger.warning("Unknown FEISHU_EVENT_TRANSPORT=%s, fallback to both", mode)
    return True


def _start_feishu_long_connection(cfg: GatewayConfig) -> None:
    """启动飞书长连接监听线程。"""
    global _FEISHU_WS_THREAD

    if _FEISHU_WS_THREAD and _FEISHU_WS_THREAD.is_alive():
        return

    if not cfg.feishu_app_id or not cfg.feishu_app_secret:
        logger.warning(
            "FEISHU_APP_ID / FEISHU_APP_SECRET not defined; skip Feishu long connection. "
            "Please export them in shell before startup."
        )
        return

    def _runner() -> None:
        try:
            lark = importlib.import_module("lark_oapi")
        except Exception as exc:
            logger.warning("Failed to import lark_oapi SDK, long connection disabled: %s", exc)
            return

        def _on_message(data: Any) -> None:
            event = getattr(data, "event", None)
            if event is None:
                return

            message = getattr(event, "message", None)
            sender = getattr(event, "sender", None)
            sender_id = getattr(sender, "sender_id", None) if sender is not None else None

            content = str(getattr(message, "content", "") or "")
            chat_id = str(getattr(message, "chat_id", "") or "").strip() or None
            message_id = str(getattr(message, "message_id", "") or "").strip() or None
            open_id = str(getattr(sender_id, "open_id", "") or "").strip() or None

            if _MAIN_LOOP is None:
                logger.warning("Main asyncio loop is unavailable; drop Feishu long-connection event")
                return

            try:
                future = asyncio.run_coroutine_threadsafe(
                    _accept_feishu_message(
                        content=content,
                        chat_id=chat_id,
                        open_id=open_id,
                        message_id=message_id,
                    ),
                    _MAIN_LOOP,
                )
                result = future.result(timeout=10)
                logger.info(
                    "Feishu long-connection event processed, accepted=%s job_id=%s",
                    result.get("accepted"),
                    result.get("job_id", ""),
                )
            except Exception as exc:
                logger.exception("Failed to process Feishu long-connection event: %s", exc)

        try:
            event_handler = lark.EventDispatcherHandler.builder(
                cfg.feishu_encrypt_key,
                cfg.feishu_verification_token,
                lark.LogLevel.INFO,
            ).register_p2_im_message_receive_v1(_on_message).build()

            client = lark.ws.Client(
                cfg.feishu_app_id,
                cfg.feishu_app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO,
            )
            logger.info("Feishu long connection starting")
            client.start()
        except Exception as exc:
            logger.exception("Feishu long connection stopped with error: %s", exc)

    _FEISHU_WS_THREAD = threading.Thread(target=_runner, name="feishu-long-conn", daemon=True)
    _FEISHU_WS_THREAD.start()


def _build_callback_headers(ctx: JobContext) -> dict[str, str]:
    """构建回调请求头。"""
    headers = {"Content-Type": "application/json"}
    if ctx.webhook_token:
        headers["Authorization"] = f"Bearer {ctx.webhook_token}"
    for key, value in (ctx.callback_headers or {}).items():
        if key and value:
            headers[key] = value
    return headers


def _post_callback(url: str, payload: dict[str, Any], headers: dict[str, str], cfg: GatewayConfig) -> None:
    """投递回调并按配置执行指数退避重试。"""
    retry = cfg.callback_retry
    last_error = None
    for attempt in range(retry):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=cfg.callback_timeout_s)
            resp.raise_for_status()
            return
        except requests.RequestException as exc:
            last_error = exc
            if attempt < retry - 1:
                time.sleep(cfg.callback_retry_backoff_s * (2 ** attempt))
    if last_error is not None:
        raise last_error


def _build_feishu_text(result: TaskResult) -> str:
    """将任务结果转换为适合飞书发送的文本。"""
    if result.status == "failed":
        return f"任务执行失败: {result.error or 'unknown error'}"
    output = result.result or {}
    text = str(output.get("reply") or "").strip() or "任务执行完成。"
    files = output.get("files") if isinstance(output, dict) else []
    if isinstance(files, list) and files:
        lines = [text, "", "文件结果:"]
        for item in files:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or "file"
            url = item.get("download_url") or ""
            lines.append(f"- {name}: {url}")
        return "\n".join(lines)
    return text


def _get_feishu_tenant_token(cfg: GatewayConfig) -> str | None:
    """获取飞书租户访问令牌。"""
    if not cfg.feishu_app_id or not cfg.feishu_app_secret:
        return None
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": cfg.feishu_app_id, "app_secret": cfg.feishu_app_secret},
        timeout=cfg.callback_timeout_s,
    )
    resp.raise_for_status()
    payload = resp.json() if resp.content else {}
    if payload.get("code") != 0:
        return None
    return payload.get("tenant_access_token")


def _send_feishu_text(cfg: GatewayConfig, receive_id: str, receive_id_type: str, text: str) -> None:
    """发送飞书文本消息。"""
    token = _get_feishu_tenant_token(cfg)
    if not token:
        return
    content = json.dumps({"text": text}, ensure_ascii=False)
    resp = requests.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"receive_id": receive_id, "msg_type": "text", "content": content},
        timeout=cfg.callback_timeout_s,
    )
    resp.raise_for_status()


async def _deliver_result(job_id: str, result: TaskResult, cfg: GatewayConfig) -> None:
    """将异步任务结果投递到 webhook 或飞书。"""
    async with _JOB_LOCK:
        ctx = _JOB_CONTEXT.get(job_id)
    if ctx is None:
        return

    payload = result.model_dump()
    if ctx.webhook_url:
        headers = _build_callback_headers(ctx)
        await asyncio.to_thread(_post_callback, ctx.webhook_url, payload, headers, cfg)

    receive_id = ctx.feishu_chat_id or ctx.feishu_user_open_id
    if receive_id:
        text = _build_feishu_text(result)
        await asyncio.to_thread(_send_feishu_text, cfg, receive_id, ctx.feishu_receive_id_type, text)


async def _run_job(
    job_id: str,
    task: str,
    output_path: str | None,
    mode: str,
    agent_hint: str | None,
    skill_hint: str | None,
    routing_strategy: str | None,
    context_id: str | None,
) -> None:
    """执行异步任务并更新任务状态。"""
    cfg = load_config()
    async with _JOB_LOCK:
        _JOB_STORE[job_id] = TaskResult(job_id=job_id, status="running")
    try:
        result = await run_gateway_task(
            task=task,
            output_path=output_path,
            mode=mode,
            agent_hint=agent_hint,
            skill_hint=skill_hint,
            routing_strategy=routing_strategy,
            context_id=context_id,
        )
        result = _decorate_result_with_files(job_id, result, request=None, cfg=cfg)
        completed = TaskResult(job_id=job_id, status="completed", result=result)
        async with _JOB_LOCK:
            _JOB_STORE[job_id] = completed
        try:
            from .config import RAG_CONFIG
            if RAG_CONFIG["task_history_enabled"]:
                from .tools import store_task_result
                await store_task_result(
                    job_id=job_id,
                    task=task,
                    reply=str((result or {}).get("reply", "")),
                    files=(result or {}).get("files", []),
                )
        except Exception as exc:
            logger.warning("Failed to store task result in RAG: %s", exc)
        try:
            await _deliver_result(job_id, completed, cfg)
        except Exception as exc:
            async with _JOB_LOCK:
                current = _JOB_STORE.get(job_id)
                if current and current.status == "completed":
                    current.error = f"callback_failed: {exc}"
    except Exception as exc:
        failed = TaskResult(
            job_id=job_id,
            status="failed",
            result={"error": str(exc)},
            error=str(exc),
        )
        async with _JOB_LOCK:
            _JOB_STORE[job_id] = failed
        try:
            await _deliver_result(job_id, failed, cfg)
        except Exception:
            pass


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查接口。"""
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """根路径默认跳转到控制台页面。"""
    return RedirectResponse(url="/console", status_code=307)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """避免浏览器默认 favicon 请求导致 404 噪音。"""
    return Response(status_code=204)


@app.get("/console", response_class=HTMLResponse, include_in_schema=False)
async def gateway_console() -> HTMLResponse:
    """返回内置网关控制台页面。"""
    if not _WEBUI_INDEX.exists():
        return HTMLResponse(
            content="<h1>Gateway Console Not Found</h1><p>missing seneschal/webui/gateway_console.html</p>",
            status_code=404,
        )
    return HTMLResponse(content=_WEBUI_INDEX.read_text(encoding="utf-8"))


@app.get("/console/settings", response_class=HTMLResponse, include_in_schema=False)
async def gateway_settings() -> HTMLResponse:
    """返回内置网关设置页面。"""
    if not _WEBUI_SETTINGS.exists():
        return HTMLResponse(
            content="<h1>Gateway Settings Not Found</h1><p>missing seneschal/webui/gateway_settings.html</p>",
            status_code=404,
        )
    return HTMLResponse(content=_WEBUI_SETTINGS.read_text(encoding="utf-8"))


def _verify_feishu_signature(
    raw_body: bytes,
    timestamp: str | None,
    nonce: str | None,
    signature: str | None,
    cfg: GatewayConfig,
) -> bool:
    """校验飞书请求签名。"""
    if not cfg.feishu_encrypt_key:
        return True
    if not timestamp or not nonce or not signature:
        return False
    string_to_sign = f"{timestamp}{nonce}{cfg.feishu_encrypt_key}{raw_body.decode('utf-8', errors='replace')}"
    digest = hmac.new(
        cfg.feishu_encrypt_key.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(digest, signature)


@app.post("/api/v1/task", response_model=TaskResult)
async def submit_task(
    request: TaskRequest,
    raw_request: Request,
    authorization: str | None = Header(default=None),
) -> TaskResult:
    """提交任务接口，支持同步与异步两种执行模式。"""
    cfg = load_config()
    _ensure_auth(authorization, cfg)

    if not request.task.strip():
        raise HTTPException(status_code=400, detail="Task must not be empty")

    if request.async_mode:
        job_id = uuid.uuid4().hex
        async with _JOB_LOCK:
            _JOB_STORE[job_id] = TaskResult(job_id=job_id, status="queued")
            _JOB_CONTEXT[job_id] = JobContext(
                webhook_url=request.webhook_url,
                webhook_token=request.webhook_token,
                callback_headers=request.callback_headers,
            )
        asyncio.create_task(
            _run_job(
                job_id,
                request.task,
                request.output_path,
                request.mode,
                request.agent_hint,
                request.skill_hint,
                request.routing_strategy,
                request.context_id,
            )
        )
        return _JOB_STORE[job_id]

    job_id = uuid.uuid4().hex
    result = await run_gateway_task(
        task=request.task,
        output_path=request.output_path,
        mode=request.mode,
        agent_hint=request.agent_hint,
        skill_hint=request.skill_hint,
        routing_strategy=request.routing_strategy,
        context_id=request.context_id,
    )
    result = _decorate_result_with_files(job_id, result, request=raw_request, cfg=cfg)
    return TaskResult(job_id=job_id, status="completed", result=result)


@app.get("/api/v1/jobs/{job_id}", response_model=TaskResult)
async def get_job(job_id: str) -> TaskResult:
    """查询异步任务状态与结果。"""
    cfg = load_config()
    async with _JOB_LOCK:
        job = _JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.result:
        job.result = _decorate_result_with_files(job_id, job.result, request=None, cfg=cfg)
    return job


@app.get("/api/v1/env")
async def get_env(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """读取项目 `.env` 内容与解析结果。"""
    cfg = load_config()
    _ensure_auth(authorization, cfg)

    content = _read_env_content()
    return {
        "path": str(_env_file_path()),
        "content": content,
        "variables": _parse_env_variables(content),
    }


@app.put("/api/v1/env")
async def put_env(
    request: EnvContentRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """覆盖更新项目 `.env` 文件内容。"""
    cfg = load_config()
    _ensure_auth(authorization, cfg)

    _write_env_content(request.content)
    content = _read_env_content()
    return {
        "ok": True,
        "path": str(_env_file_path()),
        "content": content,
        "variables": _parse_env_variables(content),
    }


@app.get("/api/v1/env/schema")
async def get_env_schema(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """获取分类 `.env` 设置 schema 与当前变量值。"""
    cfg = load_config()
    _ensure_auth(authorization, cfg)

    content = _read_env_content()
    variables = _parse_env_variables(content)
    managed, unmanaged = _split_env_variables(variables)

    return {
        "path": str(_env_file_path()),
        "schema": _ENV_SETTINGS_SCHEMA,
        "values": managed,
        "unmanaged": unmanaged,
        "variables": variables,
        "content": content,
    }


@app.put("/api/v1/env/schema")
async def put_env_schema(
    request: EnvStructuredRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """按分类变量覆盖更新 `.env`（可选保留未纳入分类的变量）。"""
    cfg = load_config()
    _ensure_auth(authorization, cfg)

    incoming_values = _sanitize_structured_values(request.values)
    managed_keys = _managed_env_keys()
    merged_values: dict[str, str] = {}
    for key in managed_keys:
        merged_values[key] = incoming_values.get(key, "")

    if request.unmanaged is not None:
        unmanaged = _sanitize_structured_values(request.unmanaged)
    elif request.preserve_unmanaged:
        current_variables = _parse_env_variables(_read_env_content())
        _, unmanaged = _split_env_variables(current_variables)
    else:
        unmanaged = {}

    new_content = _render_structured_env_content(merged_values, unmanaged)
    _write_env_content(new_content)

    content = _read_env_content()
    variables = _parse_env_variables(content)
    managed, unmanaged_saved = _split_env_variables(variables)
    return {
        "ok": True,
        "path": str(_env_file_path()),
        "schema": _ENV_SETTINGS_SCHEMA,
        "values": managed,
        "unmanaged": unmanaged_saved,
        "variables": variables,
        "content": content,
    }


@app.get("/api/v1/files/{job_id}/{file_name}")
async def get_file(job_id: str, file_name: str, authorization: str | None = Header(default=None)) -> FileResponse:
    """下载任务产出文件（需通过白名单路径校验）。"""
    cfg = load_config()
    _ensure_auth(authorization, cfg)

    async with _JOB_LOCK:
        job = _JOB_STORE.get(job_id)
    if not job or not job.result:
        raise HTTPException(status_code=404, detail="Job result not found")

    files = job.result.get("files") if isinstance(job.result, dict) else None
    if not isinstance(files, list):
        raise HTTPException(status_code=404, detail="No files for this job")

    matched = None
    for item in files:
        if isinstance(item, dict) and item.get("name") == file_name:
            matched = item
            break
    if not isinstance(matched, dict):
        raise HTTPException(status_code=404, detail="File not found")

    path = str(matched.get("path") or "").strip()
    if not path or not _can_expose_file(path, cfg):
        raise HTTPException(status_code=403, detail="File is not accessible")

    resolved = Path(path).expanduser().resolve()
    media_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
    return FileResponse(str(resolved), media_type=media_type, filename=resolved.name)


@app.post("/api/v1/feishu/events")
async def feishu_events(
    request: Request,
    x_lark_request_timestamp: str | None = Header(default=None),
    x_lark_request_nonce: str | None = Header(default=None),
    x_lark_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """飞书事件回调入口，支持 URL 校验与消息事件处理。"""
    cfg = load_config()
    raw = await request.body()
    if not _verify_feishu_signature(
        raw_body=raw,
        timestamp=x_lark_request_timestamp,
        nonce=x_lark_request_nonce,
        signature=x_lark_signature,
        cfg=cfg,
    ):
        raise HTTPException(status_code=401, detail="Invalid Feishu signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    req_type = payload.get("type")
    if req_type == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    token = str(payload.get("token") or "")
    if cfg.feishu_verification_token and token != cfg.feishu_verification_token:
        raise HTTPException(status_code=401, detail="Invalid Feishu verification token")

    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}

    return await _accept_feishu_message(
        content=str(message.get("content") or ""),
        chat_id=str(message.get("chat_id") or "").strip() or None,
        open_id=str(sender_id.get("open_id") or "").strip() or None,
        message_id=str(message.get("message_id") or "").strip() or None,
    )


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("SENESCHAL_GATEWAY_HOST", "0.0.0.0")
    port = int(os.environ.get("SENESCHAL_GATEWAY_PORT", "8090"))
    uvicorn.run("seneschal.gateway_server:app", host=host, port=port, reload=False)
