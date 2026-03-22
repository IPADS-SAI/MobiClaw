# -*- coding: utf-8 -*-
"""本地移动执行器工具封装（兼容旧版 mobi 接口）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from ..config import MOBILE_EXECUTOR_CONFIG
from ..mobile import MobileExecutor
from .mock_data import get_mock_action_result

logger = logging.getLogger(__name__)

_EXECUTOR = MobileExecutor()
_DEFAULT_OUTPUT_DIR = Path(str(MOBILE_EXECUTOR_CONFIG.get("output_dir", "outputs/mobile_exec")))


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


def _build_collect_content(
    *,
    task_desc: str,
    metadata: dict[str, Any],
    success: bool,
    message: str,
    attempt: int,
    total_attempts: int,
) -> list[TextBlock]:
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

    return [TextBlock(type="text", text=text)]


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


async def call_mobi_collect_verified(
    task_desc: str,
    max_retries: int = 0,
    output_dir: str | None = None,
) -> ToolResponse:
    """执行本地移动任务，并返回兼容结构化证据。

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

    last_error = ""
    for attempt in range(1, total_attempts + 1):
        try:
            result = _EXECUTOR.run(task=task_desc, output_dir=target_output_dir, provider=None)
            metadata = _build_execution_metadata(result.execution)
            success = bool(result.success)

            if success:
                return _build_collect_response(
                    task_desc=task_desc,
                    metadata=metadata,
                    success=True,
                    message=str(result.message or ""),
                    attempt=attempt,
                    total_attempts=total_attempts,
                )

            last_error = str(result.message or metadata.get("status_hint", "collect_failed"))
            if attempt >= total_attempts:
                return _build_collect_response(
                    task_desc=task_desc,
                    metadata=metadata,
                    success=False,
                    message=str(result.message or ""),
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
        result = _EXECUTOR.run(task=clean_task, output_dir=_resolve_output_dir(output_dir), provider=None)
        metadata = _build_execution_metadata(result.execution)

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "[MobiAgent 操作执行]\n"
                        "操作类型: natural_language_task\n"
                        f"任务: {clean_task}\n"
                        f"结果: {'成功' if result.success else '失败'}\n"
                        f"消息: {result.message}"
                    ),
                )
            ],
            metadata={
                "success": bool(result.success),
                "action_type": "natural_language_task",
                "task_desc": clean_task,
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
