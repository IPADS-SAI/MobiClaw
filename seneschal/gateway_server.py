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
import tempfile
from pathlib import Path
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import requests

from .workflows import run_gateway_task


logger = logging.getLogger(__name__)


def _load_env_file(env_path: Path) -> None:
    """从 `.env` 文件加载环境变量（仅补充未存在的键）。"""
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(Path(__file__).resolve().parents[1] / ".env")


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
    feishu_native_file_enabled: bool
    feishu_native_image_enabled: bool
    feishu_ack_enabled: bool
    feishu_group_require_mention: bool
    feishu_bot_open_id: str


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
        feishu_native_file_enabled=os.environ.get("FEISHU_NATIVE_FILE_ENABLED", "1").strip() not in {"0", "false", "False"},
        feishu_native_image_enabled=os.environ.get("FEISHU_NATIVE_IMAGE_ENABLED", "1").strip() not in {"0", "false", "False"},
        feishu_ack_enabled=os.environ.get("FEISHU_ACK_ENABLED", "1").strip() not in {"0", "false", "False"},
        feishu_group_require_mention=os.environ.get("FEISHU_GROUP_REQUIRE_MENTION", "1").strip() not in {"0", "false", "False"},
        feishu_bot_open_id=os.environ.get("FEISHU_BOT_OPEN_ID", "").strip(),
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


app = FastAPI(title="Seneschal Gateway", version="0.1.0")

_JOB_STORE: dict[str, TaskResult] = {}
_JOB_CONTEXT: dict[str, JobContext] = {}
_JOB_LOCK = asyncio.Lock()
_MAIN_LOOP: asyncio.AbstractEventLoop | None = None
_FEISHU_WS_THREAD: threading.Thread | None = None


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


def _parse_feishu_content(content: str) -> dict[str, Any]:
    """解析飞书消息内容，支持 text/image/file。"""
    parsed = (content or "").strip()
    if not parsed:
        return {"type": "text", "text": "", "raw": ""}
    try:
        payload = json.loads(parsed)
    except json.JSONDecodeError:
        return {"type": "text", "text": parsed, "raw": parsed}

    text = str(payload.get("text") or "").strip()
    image_key = str(payload.get("image_key") or "").strip()
    file_key = str(payload.get("file_key") or "").strip()
    if text:
        return {"type": "text", "text": text, "raw": parsed}
    if image_key:
        return {"type": "image", "image_key": image_key, "raw": parsed}
    if file_key:
        return {"type": "file", "file_key": file_key, "raw": parsed}
    return {"type": "unknown", "raw": parsed}


def _feishu_media_download_dir() -> Path:
    """返回飞书媒体缓存目录。"""
    configured = (os.environ.get("FEISHU_MEDIA_DOWNLOAD_DIR") or "").strip()
    path = Path(configured).expanduser() if configured else Path(tempfile.gettempdir()) / "seneschal_feishu_media"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _download_feishu_message_resource(
    cfg: GatewayConfig,
    *,
    message_id: str,
    resource_key: str,
    resource_type: str,
) -> str | None:
    """按消息资源接口下载飞书图片/文件。"""
    token = _get_feishu_tenant_token(cfg)
    if not token or not message_id or not resource_key:
        return None

    kind = resource_type if resource_type in {"image", "file"} else "file"
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{resource_key}"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params={"type": kind},
        timeout=cfg.callback_timeout_s,
    )
    resp.raise_for_status()

    content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    ext = mimetypes.guess_extension(content_type) or (".png" if kind == "image" else ".bin")
    if not ext.startswith("."):
        ext = ".bin"
    save_path = _feishu_media_download_dir() / f"{kind}_{message_id}_{resource_key}{ext}"
    save_path.write_bytes(resp.content)
    return str(save_path)


def _build_task_from_feishu_event(content: str, message_id: str | None, cfg: GatewayConfig) -> str:
    """将飞书消息转成可执行任务文本。"""
    parsed = _parse_feishu_content(content)
    msg_type = str(parsed.get("type") or "text")
    if msg_type == "text":
        return str(parsed.get("text") or "").strip()

    if msg_type in {"image", "file"} and message_id:
        key_name = "image_key" if msg_type == "image" else "file_key"
        resource_key = str(parsed.get(key_name) or "").strip()
        if not resource_key:
            return ""
        try:
            local_path = _download_feishu_message_resource(
                cfg,
                message_id=message_id,
                resource_key=resource_key,
                resource_type=msg_type,
            )
        except Exception as exc:
            logger.warning("Failed to download Feishu %s resource: %s", msg_type, exc)
            local_path = None

        if local_path:
            if msg_type == "image":
                return f"请分析这张图片内容并给出结论。图片本地路径: {local_path}"
            return f"请读取并总结这个文件内容。文件本地路径: {local_path}"
        return f"收到飞书{msg_type}消息，资源键: {resource_key}。请提示用户重试或改为文字描述。"

    return ""


def _is_image_file(path: str) -> bool:
    """判断本地文件是否为图片。"""
    mime = mimetypes.guess_type(path)[0] or ""
    return mime.startswith("image/")


def _is_text_like_file(path: str, mime_type: str | None = None) -> bool:
    """判断文件是否更适合文本回传而非原生媒体上传。"""
    mime = (mime_type or mimetypes.guess_type(path)[0] or "").strip().lower()
    if mime.startswith("text/"):
        return True
    if mime in {
        "application/json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/toml",
    }:
        return True

    suffix = Path(path).suffix.lower()
    return suffix in {
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".csv",
        ".tsv",
        ".log",
        ".xml",
    }


def _extract_open_id_from_mention(mention: Any) -> str:
    """从飞书 mention 结构中提取 open_id（兼容 dict/SDK 对象）。"""
    if isinstance(mention, dict):
        ident = mention.get("id") if isinstance(mention.get("id"), dict) else {}
        return str(ident.get("open_id") or "").strip()

    ident = getattr(mention, "id", None)
    if ident is None:
        return ""
    return str(getattr(ident, "open_id", "") or "").strip()


def _extract_mentioned_open_ids(mentions: Any, content: str) -> set[str]:
    """抽取消息中被 @ 的 open_id，缺失时尝试从内容判定是否存在 at 标签。"""
    result: set[str] = set()
    if isinstance(mentions, list):
        for mention in mentions:
            oid = _extract_open_id_from_mention(mention)
            if oid:
                result.add(oid)

    if result:
        return result

    raw = (content or "").strip()
    if "<at " in raw or "@" in raw:
        return {"__any_mention__"}
    return set()


def _should_accept_feishu_message(
    cfg: GatewayConfig,
    *,
    chat_type: str | None,
    content: str,
    mentions: Any,
) -> tuple[bool, str | None]:
    """决定是否处理飞书消息：群聊默认要求 @ 机器人。"""
    if not cfg.feishu_group_require_mention:
        return True, None

    normalized_chat_type = (chat_type or "").strip().lower()
    if normalized_chat_type and normalized_chat_type != "group":
        return True, None

    mentioned_ids = _extract_mentioned_open_ids(mentions, content)
    if not mentioned_ids:
        return False, "group_message_without_mention"

    bot_open_id = (cfg.feishu_bot_open_id or "").strip()
    if not bot_open_id:
        # 未配置 bot open_id 时，至少要求群消息存在 @ 才处理。
        return True, None

    if bot_open_id in mentioned_ids:
        return True, None
    return False, "mentioned_other_user_not_bot"


async def _enqueue_feishu_job(
    task: str,
    *,
    chat_id: str | None,
    open_id: str | None,
    message_id: str | None,
    external_context: dict[str, Any] | None = None,
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
            external_context=external_context,
        )
    )
    return job_id


async def _accept_feishu_message(
    *,
    content: str,
    chat_id: str | None,
    open_id: str | None,
    message_id: str | None,
    chat_type: str | None = None,
    mentions: Any = None,
) -> dict[str, Any]:
    """接收飞书消息并转换为网关任务。"""
    cfg = load_config()
    accepted, reason = _should_accept_feishu_message(
        cfg,
        chat_type=chat_type,
        content=content,
        mentions=mentions,
    )
    if not accepted:
        logger.info(
            "Feishu event ignored by mention filter, message_id=%s reason=%s chat_type=%s",
            message_id or "",
            reason or "",
            (chat_type or "").strip().lower() or "unknown",
        )
        return {"ok": True, "accepted": False, "reason": reason or "filtered"}

    task = _build_task_from_feishu_event(content, message_id, cfg)
    if not task:
        return {"ok": True, "accepted": False, "reason": "empty_task"}

    job_id = await _enqueue_feishu_job(
        task,
        chat_id=chat_id,
        open_id=open_id,
        message_id=message_id,
        external_context={
            "feishu": {
                "chat_id": chat_id,
                "open_id": open_id,
                "message_id": message_id,
            }
        },
    )
    if cfg.feishu_ack_enabled:
        receive_id = chat_id or open_id
        receive_type = "chat_id" if chat_id else "open_id"
        if receive_id:
            try:
                await asyncio.to_thread(_send_feishu_ack, cfg, receive_id, receive_type)
            except Exception as exc:
                logger.warning("Failed to send Feishu ack: %s", exc)
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
            chat_type = str(getattr(message, "chat_type", "") or "").strip() or None
            mentions = getattr(message, "mentions", None)
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
                        chat_type=chat_type,
                        mentions=mentions,
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
    _send_feishu_message(cfg, receive_id, receive_id_type, "text", {"text": text})


def _send_feishu_message(
    cfg: GatewayConfig,
    receive_id: str,
    receive_id_type: str,
    msg_type: str,
    content_payload: dict[str, Any],
) -> None:
    """发送飞书通用消息。"""
    token = _get_feishu_tenant_token(cfg)
    if not token:
        return
    content = json.dumps(content_payload, ensure_ascii=False)
    resp = requests.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"receive_id": receive_id, "msg_type": msg_type, "content": content},
        timeout=cfg.callback_timeout_s,
    )
    resp.raise_for_status()


def _feishu_response_debug_body(resp: requests.Response, max_len: int = 1200) -> str:
    """提取飞书响应体调试文本（优先 JSON，降级 text）。"""
    body = ""
    try:
        body = json.dumps(resp.json(), ensure_ascii=False)
    except Exception:
        body = (resp.text or "").strip()
    if len(body) > max_len:
        return body[:max_len] + "..."
    return body


def _upload_feishu_file(cfg: GatewayConfig, file_path: str) -> str | None:
    """上传本地文件到飞书，返回 file_key。"""
    token = _get_feishu_tenant_token(cfg)
    if not token:
        return None
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return None
    with path.open("rb") as fp:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            data={"file_type": "stream", "file_name": path.name},
            files={"file": (path.name, fp, mimetypes.guess_type(path.name)[0] or "application/octet-stream")},
            timeout=cfg.callback_timeout_s,
        )
    resp.raise_for_status()
    payload = resp.json() if resp.content else {}
    if payload.get("code") != 0:
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return str(data.get("file_key") or "").strip() or None


def _upload_feishu_image(cfg: GatewayConfig, image_path: str) -> str | None:
    """上传本地图片到飞书，返回 image_key。"""
    token = _get_feishu_tenant_token(cfg)
    if not token:
        return None
    path = Path(image_path)
    if not path.exists() or not path.is_file():
        return None
    with path.open("rb") as fp:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            data={"image_type": "message"},
            files={"image": (path.name, fp, mimetypes.guess_type(path.name)[0] or "image/png")},
            timeout=cfg.callback_timeout_s,
        )
    try:
        resp.raise_for_status()
    except requests.HTTPError:
        logger.warning(
            "Feishu image upload http_error status=%s path=%s response=%s",
            resp.status_code,
            image_path,
            _feishu_response_debug_body(resp),
        )
        raise
    payload = resp.json() if resp.content else {}
    if payload.get("code") != 0:
        logger.warning(
            "Feishu image upload api_error code=%s msg=%s path=%s response=%s",
            payload.get("code"),
            payload.get("msg"),
            image_path,
            _feishu_response_debug_body(resp),
        )
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return str(data.get("image_key") or "").strip() or None


def _send_feishu_file(cfg: GatewayConfig, receive_id: str, receive_id_type: str, file_path: str) -> bool:
    """发送飞书原生文件消息。"""
    file_key = _upload_feishu_file(cfg, file_path)
    if not file_key:
        return False
    _send_feishu_message(cfg, receive_id, receive_id_type, "file", {"file_key": file_key})
    return True


def _send_feishu_image(cfg: GatewayConfig, receive_id: str, receive_id_type: str, image_path: str) -> bool:
    """发送飞书原生图片消息。"""
    image_key = _upload_feishu_image(cfg, image_path)
    if not image_key:
        return False
    _send_feishu_message(cfg, receive_id, receive_id_type, "image", {"image_key": image_key})
    return True


def _send_feishu_ack(cfg: GatewayConfig, receive_id: str, receive_id_type: str) -> None:
    """发送受理反馈。"""
    _send_feishu_text(cfg, receive_id, receive_id_type, "已收到消息，正在处理中，请稍候。")


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
        files = result.result.get("files") if isinstance(result.result, dict) else []
        for item in files if isinstance(files, list) else []:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            mime_type = str(item.get("mime_type") or "").strip().lower() or None
            if not path:
                continue
            if _is_text_like_file(path, mime_type):
                logger.info("Skip native Feishu media for text-like file path=%s", path)
                continue
            try:
                if _is_image_file(path) and cfg.feishu_native_image_enabled:
                    sent = await asyncio.to_thread(_send_feishu_image, cfg, receive_id, ctx.feishu_receive_id_type, path)
                    if sent:
                        continue
                if cfg.feishu_native_file_enabled:
                    sent = await asyncio.to_thread(_send_feishu_file, cfg, receive_id, ctx.feishu_receive_id_type, path)
                    if not sent:
                        logger.warning("Failed to upload Feishu native file path=%s", path)
            except Exception as exc:
                logger.warning("Failed to send native Feishu media path=%s error=%s", path, exc)


async def _run_job(
    job_id: str,
    task: str,
    output_path: str | None,
    mode: str,
    agent_hint: str | None,
    skill_hint: str | None,
    routing_strategy: str | None,
    context_id: str | None,
    external_context: dict[str, Any] | None = None,
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
            external_context=external_context,
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


@app.on_event("startup")
async def on_startup() -> None:
    """应用启动钩子：记录主事件循环并按配置启动飞书长连接。"""
    global _MAIN_LOOP
    _MAIN_LOOP = asyncio.get_running_loop()
    cfg = load_config()

    logger.info("Feishu transport mode: %s", cfg.feishu_event_transport)

    if _should_start_feishu_long_conn(cfg):
        _start_feishu_long_connection(cfg)
    else:
        logger.info("Feishu long connection disabled by FEISHU_EVENT_TRANSPORT")


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
                None,
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
        chat_type=str(message.get("chat_type") or "").strip() or None,
        mentions=message.get("mentions"),
    )


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("SENESCHAL_GATEWAY_HOST", "0.0.0.0")
    port = int(os.environ.get("SENESCHAL_GATEWAY_PORT", "8090"))
    uvicorn.run("seneschal.gateway_server:app", host=host, port=port, reload=False)
