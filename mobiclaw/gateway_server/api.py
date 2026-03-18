# -*- coding: utf-8 -*-
"""gateway_server 的 API 路由注册。"""

from __future__ import annotations

import asyncio
import mimetypes
import shutil
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .api_env import register_env_routes
from ..mcp import get_mcp_manager

import logging
logger = logging.getLogger(__name__)


def _gateway_override(name: str, default: Any) -> Any:
    from .. import gateway_server as gateway_module

    return getattr(gateway_module, name, default)


def register_routes(app) -> None:
    exported: dict[str, Any] = {}
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}
    exported["health"] = health

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/console/chat", status_code=307)
    exported["root"] = root

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)
    exported["favicon"] = favicon

    @app.get("/console", response_class=HTMLResponse, include_in_schema=False)
    async def gateway_console() -> HTMLResponse:
        webui_index = _gateway_override("_WEBUI_INDEX", None)
        if not webui_index.exists():
            return HTMLResponse(content="<h1>Gateway Console Not Found</h1><p>missing mobiclaw/webui/gateway_console.html</p>", status_code=404)
        return HTMLResponse(content=webui_index.read_text(encoding="utf-8"))
    exported["gateway_console"] = gateway_console

    @app.get("/console/chat", response_class=HTMLResponse, include_in_schema=False)
    async def gateway_chat() -> HTMLResponse:
        webui_chat = _gateway_override("_WEBUI_CHAT", None)
        if not webui_chat.exists():
            return HTMLResponse(content="<h1>Gateway Chat Not Found</h1><p>missing mobiclaw/webui/gateway_chat.html</p>", status_code=404)
        return HTMLResponse(content=webui_chat.read_text(encoding="utf-8"))
    exported["gateway_chat"] = gateway_chat

    @app.get("/console/settings", response_class=HTMLResponse, include_in_schema=False)
    async def gateway_settings() -> HTMLResponse:
        webui_settings = _gateway_override("_WEBUI_SETTINGS", None)
        if not webui_settings.exists():
            return HTMLResponse(content="<h1>Gateway Settings Not Found</h1><p>missing mobiclaw/webui/gateway_settings.html</p>", status_code=404)
        return HTMLResponse(content=webui_settings.read_text(encoding="utf-8"))
    exported["gateway_settings"] = gateway_settings

    @app.post("/api/v1/chat/files")
    async def upload_chat_files(
        files: list[UploadFile] = File(...),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        chat_upload_root_dir = _gateway_override("_chat_upload_root_dir", None)
        sanitize_upload_name = _gateway_override("_sanitize_upload_name", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)

        if not files:
            raise HTTPException(status_code=400, detail="No files uploaded")

        upload_root = chat_upload_root_dir()
        upload_root.mkdir(parents=True, exist_ok=True)
        stored: list[dict[str, Any]] = []
        stamp_dir = upload_root / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        stamp_dir.mkdir(parents=True, exist_ok=True)

        for item in files:
            name = sanitize_upload_name(item.filename or "")
            target = stamp_dir / f"{uuid.uuid4().hex}_{name}"
            size = 0
            try:
                content = await item.read()
                target.write_bytes(content)
                size = len(content)
            finally:
                await item.close()
            stored.append({
                "name": name,
                "path": str(target.resolve()),
                "size": size,
                "mime_type": item.content_type or (mimetypes.guess_type(name)[0] or "application/octet-stream"),
            })
        return {"files": stored}
    exported["upload_chat_files"] = upload_chat_files

    @app.post("/api/v1/task", response_model=_gateway_override("TaskResult", None))
    async def submit_task(
        request: Any,
        raw_request: Request,
        authorization: str | None = Header(default=None),
    ):
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        get_active_manager = _gateway_override("get_active_manager", None)
        schedule_detection_cls = _gateway_override("ScheduleDetectionResult", None)
        task_result_cls = _gateway_override("TaskResult", None)
        inject_input_files_into_task = _gateway_override("_inject_input_files_into_task", None)
        run_job = _gateway_override("_run_job", None)
        run_gateway_task = _gateway_override("run_gateway_task", None)
        resolve_context_id = _gateway_override("_resolve_context_id", None)
        decorate_result_with_files = _gateway_override("_decorate_result_with_files", None)
        job_store = _gateway_override("_JOB_STORE", {})
        job_ctx_map = _gateway_override("_JOB_CONTEXT", {})
        job_lock = _gateway_override("_JOB_LOCK", asyncio.Lock())
        job_context_cls = _gateway_override("JobContext", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)

        if not request.task.strip():
            raise HTTPException(status_code=400, detail="Task must not be empty")

        if request.schedule and get_active_manager() is not None:
            detection = schedule_detection_cls(
                is_scheduled=True,
                core_task=request.task,
                schedule_type=request.schedule.schedule_type,
                cron_expr=request.schedule.cron_expr,
                run_at=request.schedule.run_at,
                human_description=request.schedule.description or "",
            )
            scheduled_task = await get_active_manager().add_scheduled_task(
                detection=detection,
                original_task=request.task,
                source="api",
                mode="router",
                agent_hint=request.agent_hint,
                skill_hint=request.skill_hint,
                routing_strategy=request.routing_strategy,
                web_search_enabled=request.web_search_enabled,
                job_context={
                    "webhook_url": request.webhook_url,
                    "webhook_token": request.webhook_token,
                    "callback_headers": request.callback_headers,
                },
            )
            return task_result_cls(
                job_id=scheduled_task.schedule_id,
                status="scheduled",
                result={
                    "schedule_id": scheduled_task.schedule_id,
                    "schedule_type": scheduled_task.schedule_type,
                    "human_description": scheduled_task.human_description,
                    "core_task": scheduled_task.core_task,
                    "message": f"已创建定时任务：{scheduled_task.human_description} 执行「{scheduled_task.core_task}」",
                },
            )

        effective_task, normalized_input_files = inject_input_files_into_task(request.task, request.input_files)

        if request.async_mode:
            job_id = uuid.uuid4().hex
            async with job_lock:
                job_store[job_id] = task_result_cls(job_id=job_id, status="queued")
                job_ctx_map[job_id] = job_context_cls(
                    webhook_url=request.webhook_url,
                    webhook_token=request.webhook_token,
                    callback_headers=request.callback_headers,
                )
            asyncio.create_task(
                run_job(
                    job_id,
                    effective_task,
                    request.output_path,
                    request.mode,
                    request.agent_hint,
                    request.skill_hint,
                    request.routing_strategy,
                    request.context_id,
                    None,
                    request.web_search_enabled,
                    normalized_input_files,
                )
            )
            return job_store[job_id]

        job_id = uuid.uuid4().hex
        result = await run_gateway_task(
            task=effective_task,
            output_path=request.output_path,
            mode=request.mode,
            agent_hint=request.agent_hint,
            skill_hint=request.skill_hint,
            routing_strategy=request.routing_strategy,
            context_id=request.context_id,
            web_search_enabled=request.web_search_enabled,
        )
        resolved_context_id = resolve_context_id(request.context_id, result)
        if resolved_context_id:
            result = dict(result or {})
            result.setdefault("context_id", resolved_context_id)
            result.setdefault("session_id", resolved_context_id)
        if normalized_input_files:
            result = dict(result or {})
            result["input_files"] = normalized_input_files
        result = decorate_result_with_files(job_id, result, request=raw_request, cfg=cfg)
        return task_result_cls(job_id=job_id, status="completed", result=result)
    exported["submit_task"] = submit_task

    @app.get("/api/v1/chat/sessions")
    async def list_chat_sessions(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        scan_chat_session_dirs = _gateway_override("_scan_chat_session_dirs", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)
        sessions = scan_chat_session_dirs()
        for item in sessions:
            item.pop("updated_ts", None)
            item.pop("context_alias", None)
        return {"sessions": sessions}
    exported["list_chat_sessions"] = list_chat_sessions

    @app.get("/api/v1/chat/sessions/{context_id}")
    async def get_chat_session(
        context_id: str,
        limit: int = 20,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        normalize_context_id = _gateway_override("_normalize_context_id", None)
        latest_session_dir_for_context = _gateway_override("_latest_session_dir_for_context", None)
        read_recent_session_messages = _gateway_override("_read_recent_session_messages", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)

        normalized = normalize_context_id(context_id)
        if not normalized:
            raise HTTPException(status_code=400, detail="context_id is empty")

        session_dir = latest_session_dir_for_context(normalized)
        if session_dir is None:
            raise HTTPException(status_code=404, detail="Session not found")

        try:
            stat = session_dir.stat()
            updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            updated_at = ""

        recent_messages = read_recent_session_messages(session_dir, limit=max(1, min(limit, 200)))
        return {
            "context_id": normalized,
            "session_id": normalized,
            "summary": {
                "context_id": normalized,
                "session_id": normalized,
                "dir_name": session_dir.name,
                "path": str(session_dir.resolve()),
                "updated_at": updated_at,
                "message_count": len(recent_messages),
            },
            "messages": recent_messages,
        }
    exported["get_chat_session"] = get_chat_session

    @app.delete("/api/v1/chat/sessions/{context_id}")
    async def delete_chat_session(
        context_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        normalize_context_id = _gateway_override("_normalize_context_id", None)
        scan_chat_session_dirs = _gateway_override("_scan_chat_session_dirs", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)

        normalized = normalize_context_id(context_id)
        if not normalized:
            raise HTTPException(status_code=400, detail="context_id is empty")

        matched_dirs: list[Path] = []
        for item in scan_chat_session_dirs():
            item_context_id = normalize_context_id(str(item.get("context_id") or ""))
            path_str = str(item.get("path") or "").strip()
            if item_context_id != normalized or not path_str:
                continue
            matched_dirs.append(Path(path_str))

        if not matched_dirs:
            raise HTTPException(status_code=404, detail="Session not found")

        deleted = 0
        for directory in matched_dirs:
            if not directory.exists():
                continue
            shutil.rmtree(directory, ignore_errors=False)
            deleted += 1

        return {"ok": True, "context_id": normalized, "deleted": deleted}
    exported["delete_chat_session"] = delete_chat_session

    @app.get("/api/v1/jobs/{job_id}", response_model=_gateway_override("TaskResult", None))
    async def get_job(job_id: str):
        load_config = _gateway_override("load_config", None)
        job_store = _gateway_override("_JOB_STORE", {})
        job_lock = _gateway_override("_JOB_LOCK", asyncio.Lock())
        decorate_result_with_files = _gateway_override("_decorate_result_with_files", None)
        cfg = load_config()
        async with job_lock:
            job = job_store.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.result:
            job.result = decorate_result_with_files(job_id, job.result, request=None, cfg=cfg)
        return job
    exported["get_job"] = get_job

    @app.get("/api/v1/schedules")
    async def list_schedules(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        get_active_manager = _gateway_override("get_active_manager", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)
        if get_active_manager() is None:
            return {"schedules": [], "enabled": False}
        tasks = await get_active_manager().list_tasks()
        return {"schedules": [asdict(t) for t in tasks], "enabled": True}
    exported["list_schedules"] = list_schedules

    @app.delete("/api/v1/schedules/{schedule_id}")
    async def cancel_schedule(schedule_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        get_active_manager = _gateway_override("get_active_manager", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)
        if get_active_manager() is None:
            raise HTTPException(status_code=503, detail="Scheduler not enabled")
        cancelled = await get_active_manager().cancel_task(schedule_id)
        if not cancelled:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return {"ok": True, "schedule_id": schedule_id, "status": "cancelled"}
    exported["cancel_schedule"] = cancel_schedule

    @app.get("/api/v1/files/{job_id}/{file_name}")
    async def get_file(job_id: str, file_name: str, authorization: str | None = Header(default=None)) -> FileResponse:
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        job_store = _gateway_override("_JOB_STORE", {})
        job_lock = _gateway_override("_JOB_LOCK", asyncio.Lock())
        can_expose_file = _gateway_override("_can_expose_file", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)
        async with job_lock:
            job = job_store.get(job_id)
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
        if not path or not can_expose_file(path, cfg):
            raise HTTPException(status_code=403, detail="File is not accessible")
        resolved = Path(path).expanduser().resolve()
        media_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        return FileResponse(str(resolved), media_type=media_type, filename=resolved.name)
    exported["get_file"] = get_file

    @app.post("/api/v1/feishu/events")
    async def feishu_events(
        request: Request,
        x_lark_request_timestamp: str | None = Header(default=None),
        x_lark_request_nonce: str | None = Header(default=None),
        x_lark_signature: str | None = Header(default=None),
    ) -> dict[str, Any]:
        load_config = _gateway_override("load_config", None)
        verify_feishu_signature = _gateway_override("_verify_feishu_signature", None)
        accept_feishu_message = _gateway_override("_accept_feishu_message", None)
        cfg = load_config()
        raw = await request.body()
        if not verify_feishu_signature(
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

        mode = (cfg.feishu_event_transport or "both").strip().lower()
        if mode in {"long_conn", "long-connection", "ws"}:
            logger.info(
                "Feishu webhook event skipped by transport mode, mode=%s",
                mode,
            )
            return {"ok": True, "accepted": False, "reason": "webhook_disabled_by_transport"}

        token = str(payload.get("token") or "")
        if cfg.feishu_verification_token and token != cfg.feishu_verification_token:
            raise HTTPException(status_code=401, detail="Invalid Feishu verification token")

        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
        sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}

        return await accept_feishu_message(
            content=str(message.get("content") or ""),
            chat_id=str(message.get("chat_id") or "").strip() or None,
            open_id=str(sender_id.get("open_id") or "").strip() or None,
            message_id=str(message.get("message_id") or "").strip() or None,
            source="webhook",
            chat_type=str(message.get("chat_type") or "").strip() or None,
            mentions=message.get("mentions"),
        )
    exported["feishu_events"] = feishu_events

    register_env_routes(app, exported)

    # -- MCP server management endpoints ------------------------------------

    @app.get("/api/v1/mcp/servers")
    async def list_mcp_servers(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)

        manager = get_mcp_manager()
        if manager is None:
            return {"servers": [], "enabled": False}
        return {"servers": manager.list_servers(), "enabled": True}

    exported["list_mcp_servers"] = list_mcp_servers

    @app.post("/api/v1/mcp/servers")
    async def add_mcp_server(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        manager = get_mcp_manager()
        if manager is None:
            raise HTTPException(
                status_code=503,
                detail="MCP support unavailable (mcp package not installed)",
            )

        try:
            result = await manager.add_server(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        return {"ok": True, **result}

    exported["add_mcp_server"] = add_mcp_server

    @app.delete("/api/v1/mcp/servers/{name}")
    async def remove_mcp_server(
        name: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)

        manager = get_mcp_manager()
        if manager is None:
            raise HTTPException(
                status_code=503,
                detail="MCP support unavailable (mcp package not installed)",
            )

        removed = await manager.remove_server(name)
        if not removed:
            raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

        return {"ok": True, "name": name, "status": "removed"}

    exported["remove_mcp_server"] = remove_mcp_server

    return exported
