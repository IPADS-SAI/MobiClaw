# -*- coding: utf-8 -*-
"""MobiClaw 对外任务网关服务。"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, status

from ..config import SCHEDULE_CONFIG
from ..env import load_project_env
from ..scheduler import ScheduleDetectionResult, get_active_manager, shutdown_scheduler, start_scheduler
from ..workflows import run_gateway_task
from .api import register_routes
from .devices import (
    _DEVICE_LOCK,
    _DEVICE_STORE,
    _DEVICE_STORE_FILE,
    _adb_run,
    _disconnect_all_devices,
    _ensure_adb_connected,
    _load_device_store,
    _save_device_store,
)
from .env import (
    _ENV_SETTINGS_SCHEMA,
    _env_file_path,
    _format_env_value,
    _managed_env_keys,
    _parse_env_variables,
    _read_env_content,
    _render_structured_env_content,
    _sanitize_structured_values,
    _split_env_variables,
    _write_env_content,
)
from .events import _accept_feishu_message, _enqueue_feishu_job, _start_feishu_long_connection
from .feishu import (
    _build_feishu_text,
    _build_task_from_feishu_event,
    _download_feishu_message_resource,
    _extract_mentioned_open_ids,
    _extract_open_id_from_mention,
    _feishu_response_debug_body,
    _get_feishu_tenant_token,
    _is_image_file,
    _is_text_like_file,
    _parse_feishu_content,
    _parse_feishu_text_from_content,
    _send_feishu_ack,
    _send_feishu_file,
    _send_feishu_image,
    _send_feishu_message,
    _send_feishu_text,
    _should_accept_feishu_message,
    _should_start_feishu_long_conn,
    _upload_feishu_file,
    _upload_feishu_image,
    _verify_feishu_signature,
)
from .files import (
    _build_download_url,
    _can_expose_file,
    _decorate_result_with_files,
    _default_exposed_roots,
    _feishu_media_download_dir,
    _resolve_file_root,
)
from .models import (
    DeviceHeartbeat,
    EnvContentRequest,
    EnvStructuredRequest,
    GatewayConfig,
    JobContext,
    ScheduleParam,
    TaskRequest,
    TaskResult,
    _configure_logging,
    load_config,
)
from .runtime import _build_callback_headers, _deliver_result, _execute_scheduled_job, _post_callback, _run_job
from .session import (
    _append_chat_history,
    _append_history_line,
    _build_storage_context_id,
    _chat_session_root_dir,
    _chat_upload_root_dir,
    _ensure_session_dir_for_context,
    _extract_context_alias,
    _inject_input_files_into_task,
    _latest_session_dir_for_context,
    _normalize_context_id,
    _normalize_input_files,
    _parse_chat_session_dir_name,
    _read_recent_session_messages,
    _resolve_context_id,
    _sanitize_upload_name,
    _scan_chat_session_dirs,
    _utc_now_iso,
)

logger = logging.getLogger(__name__)

load_project_env()
_configure_logging()

_JOB_STORE: dict[str, TaskResult] = {}
_JOB_CONTEXT: dict[str, JobContext] = {}
_JOB_LOCK = asyncio.Lock()
_MAIN_LOOP: asyncio.AbstractEventLoop | None = None
_FEISHU_WS_THREAD: threading.Thread | None = None
_WEBUI_CHAT = Path(__file__).resolve().parents[1] / "webui" / "gateway_chat.html"
_WEBUI_INDEX = Path(__file__).resolve().parents[1] / "webui" / "gateway_console.html"
_WEBUI_SETTINGS = Path(__file__).resolve().parents[1] / "webui" / "gateway_settings.html"


@asynccontextmanager
async def _lifespan(_: FastAPI):
    global _MAIN_LOOP
    _MAIN_LOOP = asyncio.get_running_loop()
    cfg = load_config()

    await _load_device_store()

    if SCHEDULE_CONFIG["enabled"]:
        await start_scheduler(job_executor=_execute_scheduled_job)
    else:
        logger.info("Scheduled tasks disabled by SCHEDULE_CONFIG")

    logger.info("Feishu transport mode: %s", cfg.feishu_event_transport)
    if _should_start_feishu_long_conn(cfg):
        _start_feishu_long_connection(cfg)
    else:
        logger.info("Feishu long connection disabled by FEISHU_EVENT_TRANSPORT")

    # Load saved MCP servers (non-blocking: failures are logged and skipped)
    from ..mcp import get_mcp_manager

    mcp_mgr = get_mcp_manager()
    if mcp_mgr is not None:
        await mcp_mgr.load_saved_servers()

    yield

    if mcp_mgr is not None:
        await mcp_mgr.shutdown()
    await shutdown_scheduler()
    _save_device_store()
    await _disconnect_all_devices()


app = FastAPI(title="MobiClaw Gateway", version="0.1.0", lifespan=_lifespan)


def _ensure_auth(authorization: str | None, cfg: GatewayConfig) -> None:
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


globals().update(register_routes(app))
