# -*- coding: utf-8 -*-
"""本地移动执行器工具封装（兼容旧版 mobi 接口）。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Any

from agentscope.message import ImageBlock, TextBlock
from agentscope.tool import ToolResponse

from ..config import MOBILE_EXECUTOR_CONFIG, MOBILE_TASK_BACKEND_CONFIG
from ..mobile import MobileExecutor
from .mock_data import get_mock_action_result

logger = logging.getLogger(__name__)

_EXECUTOR = MobileExecutor()
_DEFAULT_OUTPUT_DIR = Path(str(MOBILE_EXECUTOR_CONFIG.get("output_dir", "outputs/mobile_exec")))
_REMOTE_BACKEND_TTL = timedelta(seconds=45)


def _resolve_output_dir(output_dir: str | None = None) -> str:
    target = (output_dir or "").strip()
    if not target:
        target = str(_DEFAULT_OUTPUT_DIR)
    path = Path(target)
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _build_execution_metadata(execution: dict[str, Any]) -> dict[str, Any]:
    """构造 mobi 工具稳定输出的 metadata 结构。

    MobileExecutor 返回的是嵌套 execution 树；上层工具只需要少量常用摘要字段，
    同时也需要保留完整 execution 供下游继续读取。这里统一完成映射，避免调用点
    重复做字段抽取和格式整理。

    Args:
        execution: MobileExecutor 返回的原始 execution 结果。
    """
    summary = execution.get("summary") if isinstance(execution.get("summary"), dict) else {}
    history = execution.get("history") if isinstance(execution.get("history"), dict) else {}
    artifacts = execution.get("artifacts") if isinstance(execution.get("artifacts"), dict) else {}

    reasonings = history.get("reasonings") if isinstance(history.get("reasonings"), list) else []
    images = artifacts.get("images") if isinstance(artifacts.get("images"), list) else []
    run_dir = str(execution.get("run_dir", ""))
    index_file = str(execution.get("index_file", "")) or (str(Path(run_dir) / "execution_result.json") if run_dir else "")

    return {
        "execution": execution,
        "final_image_path": str(summary.get("final_screenshot_path", images[-1] if images else "")),
        "last_reasoning": str(reasonings[-1]) if reasonings else "",
        "action_count": int(summary.get("action_count", 0) or 0),
        "step_count": int(summary.get("step_count", 0) or 0),
        "status_hint": str(summary.get("status_hint", "")),
        "run_dir": run_dir,
        "index_file": index_file,
    }


def _image_extension_from_mime(mime_type: str) -> str:
    normalized = str(mime_type or "").strip().lower()
    if normalized == "image/png":
        return ".png"
    if normalized == "image/webp":
        return ".webp"
    return ".jpg"


def _write_remote_attachment(
    image_payload: dict[str, Any],
    target_dir: Path,
    file_stem: str,
) -> str:
    base64_data = str(image_payload.get("base64_data") or "").strip()
    if not base64_data:
        return ""
    try:
        image_bytes = base64.b64decode(base64_data, validate=True)
    except Exception:
        logger.warning("remote mobile image decode failed: %s", file_stem, exc_info=True)
        return ""
    suffix = _image_extension_from_mime(str(image_payload.get("mime_type") or ""))
    target_path = target_dir / f"{file_stem}{suffix}"
    target_path.write_bytes(image_bytes)
    return str(target_path)


def _materialize_remote_execution(
    payload: dict[str, Any],
    output_dir: str,
    task_id: str,
) -> dict[str, Any]:
    execution_raw = payload.get("execution")
    if not isinstance(execution_raw, dict):
        return {}

    execution = deepcopy(execution_raw)
    final_image = payload.get("final_image") if isinstance(payload.get("final_image"), dict) else {}
    recent_images_raw = payload.get("recent_images")
    recent_images = recent_images_raw if isinstance(recent_images_raw, list) else []
    has_embedded_images = bool(str(final_image.get("base64_data") or "").strip()) or any(
        isinstance(item, dict) and str(item.get("base64_data") or "").strip()
        for item in recent_images
    )
    if not has_embedded_images:
        return execution

    run_dir = Path(output_dir) / f"remote_{task_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = execution.get("summary")
    if not isinstance(summary, dict):
        summary = {}
        execution["summary"] = summary
    artifacts = execution.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
        execution["artifacts"] = artifacts

    final_image_path = _write_remote_attachment(final_image, run_dir, "final_screenshot")
    image_paths: list[str] = []
    for index, image_payload in enumerate(recent_images, start=1):
        if not isinstance(image_payload, dict):
            continue
        image_path = _write_remote_attachment(image_payload, run_dir, f"step_{index:02d}")
        if image_path:
            image_paths.append(image_path)
    if final_image_path and final_image_path not in image_paths:
        image_paths.append(final_image_path)

    if final_image_path:
        summary["final_screenshot_path"] = final_image_path
    artifacts["images"] = image_paths
    execution["run_dir"] = str(run_dir)
    execution["index_file"] = str(run_dir / "execution_result.json")
    (run_dir / "execution_result.json").write_text(
        json.dumps(execution, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return execution


def _build_collect_content(
    *,
    task_desc: str,
    metadata: dict[str, Any],
    success: bool,
    message: str,
    attempt: int,
    total_attempts: int,
) -> list[TextBlock | ImageBlock]:
    """构造 collect 工具返回给agent模型阅读的内容块。

    Args:
        task_desc: 手机任务描述。
        metadata: 已整理后的执行摘要 metadata。
        success: 本次执行是否成功。
        message: 执行器返回的消息文本。
        attempt: 当前尝试次数。
        total_attempts: 总尝试次数。
    """
    if success:
        text = (
            "[MobiAgent 收集完成(需Agent验证)]\n"
            f"任务: {task_desc}\n"
            f"Mobi手机任务执行器状态提示: {metadata['status_hint']}\n"
            f"最后推理: {metadata['last_reasoning']}"
        )
    else:
        text = (
            "[MobiAgent 收集失败]\n"
            f"任务: {task_desc}\n"
            f"message: {message}\n"
            f"status_hint: {metadata.get('status_hint', '')}\n"
            f"run_dir: {metadata.get('run_dir', '')}\n"
            f"attempt: {attempt}/{total_attempts}"
        )

    content: list[TextBlock | ImageBlock] = [TextBlock(type="text", text=text)]
    image_block = _build_image_block_from_path(str(metadata.get("final_image_path", "") or ""))
    if image_block is not None:
        content.append(image_block)
    return content


def _build_image_block_from_path(image_ref: str) -> ImageBlock | None:
    value = str(image_ref or "").strip()
    if not value:
        return None
    return {"type": "image", "source": {"type": "url", "url": value}}


def _build_collect_response(
    *,
    task_desc: str,
    metadata: dict[str, Any],
    success: bool,
    message: str,
    attempt: int,
    total_attempts: int,
) -> ToolResponse:
    """构造单次 collect 尝试的最终 ToolResponse。

    返回约定：
    - content: 文本块
    - metadata: execution 树 + 上层常用摘要字段

    Args:
        task_desc: 手机任务描述。
        metadata: 已整理后的执行摘要 metadata。
        success: 本次执行是否成功。
        message: 执行器返回的消息文本。
        attempt: 当前尝试次数。
        total_attempts: 总尝试次数。
    """
    return ToolResponse(
        content=_build_collect_content(
            task_desc=task_desc,
            metadata=metadata,
            success=success,
            message=message,
            attempt=attempt,
            total_attempts=total_attempts,
        ),
        metadata={
            "success": success,
            "requires_agent_validation": True,
            "attempt": attempt,
            "attempt_total": total_attempts,
            "task": task_desc,
            **metadata,
        },
    )


def _mobile_execution_mode() -> str:
    mode = str(MOBILE_TASK_BACKEND_CONFIG.get("mode", "local") or "local").strip().lower()
    return mode if mode in {"local", "remote"} else "local"


def _get_gateway_mobile_runtime() -> tuple[dict[str, Any], Any, dict[str, Any], Any]:
    from .. import gateway_server as gateway_module

    return (
        getattr(gateway_module, "_MOBILE_TASK_STORE"),
        getattr(gateway_module, "_MOBILE_TASK_LOCK"),
        getattr(gateway_module, "_MOBILE_BACKEND_STORE"),
        getattr(gateway_module, "_MOBILE_BACKEND_LOCK"),
    )


def _fresh_remote_backend_count(backend_store: dict[str, Any]) -> int:
    cutoff = datetime.now(timezone.utc) - _REMOTE_BACKEND_TTL
    fresh = 0
    stale_ids: list[str] = []
    for backend_id, record in backend_store.items():
        if not isinstance(record, dict):
            stale_ids.append(str(backend_id))
            continue
        last_seen_raw = str(record.get("last_seen") or "").strip()
        try:
            last_seen = datetime.fromisoformat(last_seen_raw)
        except ValueError:
            stale_ids.append(str(backend_id))
            continue
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        if last_seen >= cutoff:
            fresh += 1
        else:
            stale_ids.append(str(backend_id))
    for backend_id in stale_ids:
        backend_store.pop(backend_id, None)
    return fresh


def _run_remote_mobile_task(task_desc: str, output_dir: str, max_steps: int) -> tuple[bool, str, dict[str, Any]]:
    import uuid
    from datetime import datetime, timezone

    task_store, task_lock, backend_store, backend_lock = _get_gateway_mobile_runtime()

    async def _enqueue_task() -> str:
        async with backend_lock:
            if _fresh_remote_backend_count(backend_store) <= 0:
                raise RuntimeError("no connected mobile backend is available")
        task_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "task_id": task_id,
            "task_desc": task_desc,
            "provider": str(MOBILE_EXECUTOR_CONFIG.get("provider", "mobiagent")),
            "max_steps": max_steps,
            "timeout_s": int(MOBILE_TASK_BACKEND_CONFIG.get("timeout_s", 900) or 900),
            "output_schema": "seneschal_mobile_exec_v1",
            "context": None,
            "output_path": output_dir,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "progress": None,
            "result": None,
            "message": "",
        }
        async with task_lock:
            task_store[task_id] = record
        return task_id

    async def _read_task(task_id: str) -> dict[str, Any] | None:
        async with task_lock:
            item = task_store.get(task_id)
            return dict(item) if isinstance(item, dict) else None

    task_id = asyncio.run(_enqueue_task())

    timeout_s = float(MOBILE_TASK_BACKEND_CONFIG.get("timeout_s", 900) or 900)
    poll_interval_s = float(MOBILE_TASK_BACKEND_CONFIG.get("poll_interval_s", 2.0) or 2.0)
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        current = asyncio.run(_read_task(task_id)) or {}
        status = str(current.get("status") or "").lower()
        if status in {"completed", "failed"}:
            payload = current.get("result") if isinstance(current.get("result"), dict) else current
            success = bool(payload.get("success", status == "completed"))
            message = str(payload.get("message") or current.get("message") or status)
            execution = _materialize_remote_execution(payload, output_dir, task_id)
            if not execution:
                execution = {
                    "schema_version": "seneschal_mobile_exec_v1",
                    "run_dir": str(payload.get("run_dir") or ""),
                    "index_file": str(payload.get("index_file") or ""),
                    "summary": {
                        "status_hint": status,
                        "step_count": 0,
                        "action_count": 0,
                        "final_screenshot_path": "",
                        "elapsed_time": 0.0,
                    },
                    "artifacts": {"images": [], "hierarchies": [], "overlays": [], "logs": []},
                    "history": {"actions": [], "reacts": [], "reasonings": []},
                    "ocr": {"source": "none", "by_step": [], "full_text": ""},
                }
            return success, message, execution
        time.sleep(max(0.2, poll_interval_s))

    raise TimeoutError(f"remote mobile task timed out after {timeout_s:.0f}s")


async def call_mobi_collect_verified(
    task_desc: str,
    max_retries: int = 0,
    output_dir: str | None = None,
) -> ToolResponse:
    """执行本地/远端移动任务，并返回兼容结构化证据。

    Args:
        task_desc: 待执行的手机任务描述。
        max_retries: 失败后的额外重试次数，`0` 表示仅执行 `1` 次。
        output_dir: 可选输出目录，用于保存执行产物。
    """
    try:
        retry_cap = max(0, int(max_retries))
    except (TypeError, ValueError):
        retry_cap = 0
    total_attempts = retry_cap + 1
    target_output_dir = _resolve_output_dir(output_dir)
    execution_mode = _mobile_execution_mode()

    last_error = ""
    for attempt in range(1, total_attempts + 1):
        try:
            if execution_mode == "remote":
                success, message, execution = await asyncio.to_thread(
                    _run_remote_mobile_task,
                    task_desc,
                    target_output_dir,
                    int(MOBILE_EXECUTOR_CONFIG.get("max_steps", 40) or 40),
                )
            else:
                result = await asyncio.to_thread(
                    _EXECUTOR.run,
                    task_desc,
                    target_output_dir,
                    None,
                )
                success = bool(result.success)
                message = str(result.message or "")
                execution = result.execution

            metadata = _build_execution_metadata(execution)

            if success:
                return _build_collect_response(
                    task_desc=task_desc,
                    metadata=metadata,
                    success=True,
                    message=message,
                    attempt=attempt,
                    total_attempts=total_attempts,
                )

            last_error = str(message or metadata.get("status_hint", "collect_failed"))
            if attempt >= total_attempts:
                return _build_collect_response(
                    task_desc=task_desc,
                    metadata=metadata,
                    success=False,
                    message=message,
                    attempt=attempt,
                    total_attempts=total_attempts,
                )
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.exception("local mobile collect failed: attempt=%s/%s", attempt, total_attempts)
            if attempt >= total_attempts:
                return ToolResponse(
                    content=[TextBlock(type="text", text=f"[MobiAgent 调用失败] 错误: {exc}")],
                    metadata={
                        "success": False,
                        "requires_agent_validation": True,
                        "attempt": attempt,
                        "attempt_total": total_attempts,
                        "error": str(exc),
                    },
                )

    return ToolResponse(
        content=[TextBlock(type="text", text=f"[MobiAgent 调用失败] 错误: {last_error or 'unknown error'}")],
        metadata={"success": False, "requires_agent_validation": True, "error": last_error or "unknown error"},
    )


async def call_mobi_action_task(task_desc: str, output_dir: str | None = None) -> ToolResponse:
    """按自然语言任务描述执行一次手机 GUI 操作。

    Args:
        task_desc: 完整的自然语言手机任务描述。
        output_dir: 可选输出目录，用于保存执行产物。
    """
    clean_task = str(task_desc or "").strip()
    if not clean_task:
        return ToolResponse(
            content=[TextBlock(type="text", text="[MobiAgent 调用失败] 错误: task_desc 不能为空")],
            metadata={"success": False, "error": "empty_task_desc"},
        )

    logger.info("mobi.action.request task_desc=%s", clean_task)
    try:
        target_output_dir = _resolve_output_dir(output_dir)
        if _mobile_execution_mode() == "remote":
            success, message, execution = await asyncio.to_thread(
                _run_remote_mobile_task,
                clean_task,
                target_output_dir,
                int(MOBILE_EXECUTOR_CONFIG.get("max_steps", 40) or 40),
            )
        else:
            result = await asyncio.to_thread(_EXECUTOR.run, clean_task, target_output_dir, None)
            success = bool(result.success)
            message = str(result.message or "")
            execution = result.execution
        metadata = _build_execution_metadata(execution)

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "[MobiAgent 操作执行]\n"
                        "操作类型: natural_language_task\n"
                        f"任务: {clean_task}\n"
                        f"结果: {'成功' if success else '失败'}\n"
                        f"消息: {message}"
                    ),
                )
            ],
            metadata={
                "success": bool(success),
                "action_type": "natural_language_task",
                "task_desc": clean_task,
                "execution_mode": _mobile_execution_mode(),
                **metadata,
            },
        )

    except Exception as exc:  # noqa: BLE001
        mock_result = get_mock_action_result("natural_language_task", clean_task)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "[MobiAgent Mock 模式 - 操作执行]\n"
                        "操作类型: natural_language_task\n"
                        f"任务: {clean_task}\n"
                        f"模拟执行结果: {mock_result}\n"
                        f"(实际错误: {str(exc)[:120]})"
                    ),
                )
            ],
            metadata={"mock": True, "result": mock_result, "task_desc": clean_task},
        )
