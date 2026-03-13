# -*- coding: utf-8 -*-
"""gateway_server 的飞书事件接入逻辑。"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("seneschal.gateway_server")

_FEISHU_MESSAGE_DEDUP: dict[str, float] = {}
_FEISHU_MESSAGE_DEDUP_LOCK = asyncio.Lock()


def _feishu_message_dedup_ttl_s() -> int:
    raw = (os.environ.get("FEISHU_MESSAGE_DEDUP_TTL_S") or "600").strip()
    try:
        return max(30, int(raw))
    except ValueError:
        return 600


def _feishu_message_dedup_max_items() -> int:
    raw = (os.environ.get("FEISHU_MESSAGE_DEDUP_MAX_ITEMS") or "10000").strip()
    try:
        return max(100, int(raw))
    except ValueError:
        return 10000


async def _is_duplicate_feishu_message(message_id: str | None) -> bool:
    normalized = str(message_id or "").strip()
    if not normalized:
        return False

    now = time.time()
    ttl_s = _feishu_message_dedup_ttl_s()
    expire_before = now - ttl_s

    async with _FEISHU_MESSAGE_DEDUP_LOCK:
        stale_keys = [k for k, ts in _FEISHU_MESSAGE_DEDUP.items() if ts < expire_before]
        for key in stale_keys:
            _FEISHU_MESSAGE_DEDUP.pop(key, None)

        seen_at = _FEISHU_MESSAGE_DEDUP.get(normalized)
        if seen_at is not None and seen_at >= expire_before:
            return True

        _FEISHU_MESSAGE_DEDUP[normalized] = now
        max_items = _feishu_message_dedup_max_items()
        if len(_FEISHU_MESSAGE_DEDUP) > max_items:
            oldest = min(_FEISHU_MESSAGE_DEDUP, key=_FEISHU_MESSAGE_DEDUP.get)
            _FEISHU_MESSAGE_DEDUP.pop(oldest, None)
        return False


def _gateway_override(name: str, default: Any) -> Any:
    from .. import gateway_server as gateway_module

    return getattr(gateway_module, name, default)


async def _enqueue_feishu_job(
    task: str,
    *,
    output_path: str | None,
    chat_id: str | None,
    open_id: str | None,
    message_id: str | None,
    external_context: dict[str, Any] | None = None,
) -> str:
    job_id = uuid.uuid4().hex
    task_result_cls = _gateway_override("TaskResult", None)
    job_context_cls = _gateway_override("JobContext", None)
    job_store = _gateway_override("_JOB_STORE", {})
    job_ctx_map = _gateway_override("_JOB_CONTEXT", {})
    job_lock = _gateway_override("_JOB_LOCK", asyncio.Lock())
    run_job = _gateway_override("_run_job", None)
    async with job_lock:
        job_store[job_id] = task_result_cls(job_id=job_id, status="queued")
        job_ctx_map[job_id] = job_context_cls(
            feishu_chat_id=chat_id,
            feishu_user_open_id=open_id,
            feishu_message_id=message_id,
            feishu_receive_id_type="chat_id" if chat_id else "open_id",
        )
    asyncio.create_task(
        run_job(
            job_id,
            task,
            output_path=output_path,
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
    source: str = "unknown",
    chat_type: str | None = None,
    mentions: Any = None,
) -> dict[str, Any]:
    load_config = _gateway_override("load_config", None)
    should_accept = _gateway_override("_should_accept_feishu_message", None)
    build_task = _gateway_override("_build_task_from_feishu_event", None)
    send_ack = _gateway_override("_send_feishu_ack", None)
    cfg = load_config()
    accepted, reason = should_accept(
        cfg,
        chat_type=chat_type,
        content=content,
        mentions=mentions,
    )
    if not accepted:
        logger.info(
            "Feishu event ignored by mention filter, source=%s message_id=%s reason=%s chat_type=%s",
            source,
            message_id or "",
            reason or "",
            (chat_type or "").strip().lower() or "unknown",
        )
        return {"ok": True, "accepted": False, "reason": reason or "filtered"}

    if await _is_duplicate_feishu_message(message_id):
        logger.info(
            "Feishu duplicate event ignored, source=%s message_id=%s",
            source,
            message_id or "",
        )
        return {"ok": True, "accepted": False, "reason": "duplicate_message_id"}

    output_root_raw = (os.environ.get("SENESCHAL_FILE_WRITE_ROOT") or "").strip()
    if output_root_raw:
        output_root = Path(output_root_raw).expanduser()
    else:
        output_root = Path(__file__).resolve().parents[2] / "outputs"
    job_name = "job_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    reserved_job_dir = output_root / job_name
    reserved_tmp_dir = reserved_job_dir / "tmp"
    reserved_tmp_dir.mkdir(parents=True, exist_ok=True)
    reserved_output_path = reserved_job_dir / "final_output.md"
    feishu_download_dir = reserved_job_dir / "feishu_media"

    task = build_task(
        content,
        message_id,
        cfg,
        download_dir=str(feishu_download_dir),
    )
    if not task:
        return {"ok": True, "accepted": False, "reason": "empty_task"}

    job_id = await _enqueue_feishu_job(
        task,
        output_path=str(reserved_output_path),
        chat_id=chat_id,
        open_id=open_id,
        message_id=message_id,
        external_context={"feishu": {"chat_id": chat_id, "open_id": open_id, "message_id": message_id}},
    )
    if cfg.feishu_ack_enabled:
        receive_id = chat_id or open_id
        receive_type = "chat_id" if chat_id else "open_id"
        if receive_id:
            try:
                await asyncio.to_thread(send_ack, cfg, receive_id, receive_type)
            except Exception as exc:
                logger.warning("Failed to send Feishu ack: %s", exc)
    logger.info(
        "Feishu event accepted, source=%s job_id=%s message_id=%s",
        source,
        job_id,
        message_id or "",
    )
    return {"ok": True, "accepted": True, "job_id": job_id}


def _start_feishu_long_connection(cfg) -> None:
    feishu_ws_thread = _gateway_override("_FEISHU_WS_THREAD", None)
    main_loop = _gateway_override("_MAIN_LOOP", None)
    if feishu_ws_thread and feishu_ws_thread.is_alive():
        return

    if not cfg.feishu_app_id or not cfg.feishu_app_secret:
        logger.warning(
            "FEISHU_APP_ID / FEISHU_APP_SECRET not defined; skip Feishu long connection. Please export them in shell before startup."
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

            if main_loop is None:
                logger.warning("Main asyncio loop is unavailable; drop Feishu long-connection event")
                return

            try:
                future = asyncio.run_coroutine_threadsafe(
                    _accept_feishu_message(
                        content=content,
                        chat_id=chat_id,
                        open_id=open_id,
                        message_id=message_id,
                        source="long_conn",
                        chat_type=chat_type,
                        mentions=mentions,
                    ),
                    main_loop,
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

    thread = threading.Thread(target=_runner, name="feishu-long-conn", daemon=True)
    from .. import gateway_server as gateway_module

    gateway_module._FEISHU_WS_THREAD = thread
    thread.start()
