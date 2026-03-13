# -*- coding: utf-8 -*-
"""gateway_server 的任务运行时逻辑。"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

import requests

logger = logging.getLogger("seneschal.gateway_server")


def _gateway_override(name: str, default: Any) -> Any:
    from .. import gateway_server as gateway_module

    return getattr(gateway_module, name, default)


async def _execute_scheduled_job(
    *,
    schedule_id: str,
    task: str,
    mode: str,
    agent_hint: str | None,
    skill_hint: str | None,
    routing_strategy: str | None,
    context_id: str | None,
    web_search_enabled: bool,
    job_context: dict[str, Any],
) -> str:
    job_id = uuid.uuid4().hex
    task_result_cls = _gateway_override("TaskResult", None)
    job_context_cls = _gateway_override("JobContext", None)
    job_store = _gateway_override("_JOB_STORE", {})
    job_ctx_map = _gateway_override("_JOB_CONTEXT", {})
    job_lock = _gateway_override("_JOB_LOCK", asyncio.Lock())
    run_job = _gateway_override("_run_job", None)
    external_context = None
    async with job_lock:
        job_store[job_id] = task_result_cls(job_id=job_id, status="queued")
        ctx = job_context_cls(
            webhook_url=job_context.get("webhook_url", None),
            webhook_token=job_context.get("webhook_token", None),
            callback_headers=job_context.get("callback_headers", None),
            feishu_chat_id=job_context.get("feishu_chat_id", None),
            feishu_user_open_id=job_context.get("feishu_user_open_id", None),
            feishu_message_id=job_context.get("feishu_message_id", None),
            feishu_receive_id_type=job_context.get("feishu_receive_id_type", "chat_id"),
        )
        job_ctx_map[job_id] = ctx
        if ctx.feishu_chat_id or ctx.feishu_user_open_id or ctx.feishu_message_id:
            external_context = {
                "feishu": {
                    "chat_id": ctx.feishu_chat_id,
                    "open_id": ctx.feishu_user_open_id,
                    "message_id": ctx.feishu_message_id,
                }
            }
    asyncio.create_task(
        run_job(
            job_id,
            task,
            output_path=None,
            mode=mode,
            agent_hint=agent_hint,
            skill_hint=skill_hint,
            routing_strategy=routing_strategy,
            context_id=context_id,
            external_context=external_context,
            web_search_enabled=web_search_enabled,
        )
    )
    logger.info("Scheduled task %s triggered, job_id=%s", schedule_id, job_id)
    return job_id


def _build_callback_headers(ctx) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if ctx.webhook_token:
        headers["Authorization"] = f"Bearer {ctx.webhook_token}"
    for key, value in (ctx.callback_headers or {}).items():
        if key and value:
            headers[key] = value
    return headers


def _post_callback(url: str, payload: dict[str, Any], headers: dict[str, str], cfg) -> None:
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


async def _deliver_result(job_id: str, result, cfg) -> None:
    job_lock = _gateway_override("_JOB_LOCK", asyncio.Lock())
    job_ctx_map = _gateway_override("_JOB_CONTEXT", {})
    async with job_lock:
        ctx = job_ctx_map.get(job_id)
    if ctx is None:
        logger.warning("Job context not found for job_id=%s. Skipping result delivery", job_id)
        return

    payload = result.model_dump()
    if ctx.webhook_url:
        headers = _build_callback_headers(ctx)
        await asyncio.to_thread(_post_callback, ctx.webhook_url, payload, headers, cfg)

    receive_id = ctx.feishu_chat_id or ctx.feishu_user_open_id
    if receive_id:
        build_feishu_text = _gateway_override("_build_feishu_text", None)
        send_feishu_text = _gateway_override("_send_feishu_text", None)
        is_text_like_file = _gateway_override("_is_text_like_file", None)
        is_image_file = _gateway_override("_is_image_file", None)
        send_feishu_image = _gateway_override("_send_feishu_image", None)
        send_feishu_file = _gateway_override("_send_feishu_file", None)
        text = build_feishu_text(result)
        await asyncio.to_thread(send_feishu_text, cfg, receive_id, ctx.feishu_receive_id_type, text)
        files = result.result.get("files") if isinstance(result.result, dict) else []
        for item in files if isinstance(files, list) else []:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            mime_type = str(item.get("mime_type") or "").strip().lower() or None
            if not path:
                continue
            if is_text_like_file(path, mime_type):
                logger.info("Skip native Feishu media for text-like file path=%s", path)
                continue
            try:
                if is_image_file(path) and cfg.feishu_native_image_enabled:
                    sent = await asyncio.to_thread(send_feishu_image, cfg, receive_id, ctx.feishu_receive_id_type, path)
                    if sent:
                        continue
                if cfg.feishu_native_file_enabled:
                    sent = await asyncio.to_thread(send_feishu_file, cfg, receive_id, ctx.feishu_receive_id_type, path)
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
    web_search_enabled: bool = True,
    input_files: list[str] | None = None,
) -> None:
    load_config = _gateway_override("load_config", None)
    utc_now_iso = _gateway_override("_utc_now_iso", None)
    task_result_cls = _gateway_override("TaskResult", None)
    job_store = _gateway_override("_JOB_STORE", {})
    job_lock = _gateway_override("_JOB_LOCK", asyncio.Lock())
    run_gateway_task = _gateway_override("run_gateway_task", None)
    resolve_context_id = _gateway_override("_resolve_context_id", None)
    normalize_input_files = _gateway_override("_normalize_input_files", None)
    decorate_result_with_files = _gateway_override("_decorate_result_with_files", None)
    deliver_result = _gateway_override("_deliver_result", _deliver_result)
    cfg = load_config()
    async with job_lock:
        job_store[job_id] = task_result_cls(
            job_id=job_id,
            status="running",
            result={
                "progress": {
                    "updated_at": utc_now_iso(),
                    "planner_monitor": {"enabled": False, "events": [], "current_plan": None},
                    "orchestrator_events": [],
                },
            },
        )

    async def _update_job_progress(payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        async with job_lock:
            current = job_store.get(job_id)
            if current is None or current.status != "running":
                return
            current_result = current.result if isinstance(current.result, dict) else {}
            progress = current_result.get("progress")
            if not isinstance(progress, dict):
                progress = {}
            progress["updated_at"] = utc_now_iso()
            channel = str(payload.get("channel") or "").strip().lower()
            if channel == "planner_monitor":
                planner = payload.get("planner")
                if isinstance(planner, dict):
                    progress["planner_monitor"] = planner
                progress["session_id"] = str(payload.get("session_id") or "")
                progress["mode"] = str(payload.get("mode") or mode or "")
            elif channel == "orchestrator_progress":
                events = progress.get("orchestrator_events")
                if not isinstance(events, list):
                    events = []
                events.append(payload)
                progress["orchestrator_events"] = events[-120:]
            else:
                progress.update(payload)
            current_result["progress"] = progress
            current.result = current_result
            job_store[job_id] = current

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
            web_search_enabled=web_search_enabled,
            progress_callback=_update_job_progress,
        )
        resolved_context_id = resolve_context_id(context_id, result)
        if resolved_context_id:
            result = dict(result or {})
            result.setdefault("context_id", resolved_context_id)
            result.setdefault("session_id", resolved_context_id)
        normalized_input_files = normalize_input_files(input_files)
        if normalized_input_files:
            result = dict(result or {})
            result["input_files"] = normalized_input_files
        result = decorate_result_with_files(job_id, result, request=None, cfg=cfg)
        completed = task_result_cls(job_id=job_id, status="completed", result=result)
        async with job_lock:
            job_store[job_id] = completed
        try:
            from ..config import RAG_CONFIG

            if RAG_CONFIG["task_history_enabled"]:
                from ..tools import store_task_result

                await store_task_result(
                    job_id=job_id,
                    task=task,
                    reply=str((result or {}).get("reply", "")),
                    files=(result or {}).get("files", []),
                )
        except Exception as exc:
            logger.warning("Failed to store task result in RAG: %s", exc)
        try:
            await deliver_result(job_id, completed, cfg)
        except Exception as exc:
            async with job_lock:
                current = job_store.get(job_id)
                if current and current.status == "completed":
                    current.error = f"callback_failed: {exc}"
    except Exception as exc:
        resolved_context_id = resolve_context_id(context_id, None)
        failed = task_result_cls(
            job_id=job_id,
            status="failed",
            result={"error": str(exc), "context_id": resolved_context_id, "session_id": resolved_context_id},
            error=str(exc),
        )
        async with job_lock:
            job_store[job_id] = failed
        try:
            await deliver_result(job_id, failed, cfg)
        except Exception:
            pass
