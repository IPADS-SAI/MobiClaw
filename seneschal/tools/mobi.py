# -*- coding: utf-8 -*-
"""本地移动执行器工具封装（兼容旧版 mobi 接口）。"""

from __future__ import annotations

import json
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


def _build_task_from_action(action_type: str, params: dict[str, Any]) -> str:
    if action_type == "add_calendar_event":
        title = params.get("title", "日程")
        date = params.get("date", "")
        time_str = params.get("time", "")
        return f"打开系统日历并创建日程：{title}。日期{date} 时间{time_str}。"
    if action_type == "send_message":
        target = params.get("target", params.get("contact", "对方"))
        content = params.get("content", params.get("text", ""))
        return f"通过微信给{target}发送消息：{content}"
    if action_type == "set_reminder":
        content = params.get("content", params.get("title", "提醒事项"))
        remind_time = params.get("time", "")
        date = params.get("date", "")
        return f"在系统提醒事项中创建提醒：{content}。日期{date} 时间{remind_time}。"
    if action_type == "open_app":
        app_name = params.get("app", params.get("app_name", ""))
        return f"打开应用 {app_name}"
    return f"完成以下任务：{json.dumps({'action_type': action_type, 'params': params}, ensure_ascii=False)}"

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


async def call_mobi_action(action_type: str, payload: str, output_dir: str | None = None) -> ToolResponse:
    """指挥本地移动执行器执行手机 GUI 操作。

    Args:
        action_type: 操作类型标识。
        payload: JSON 字符串或原始文本参数。
        output_dir: 可选输出目录，用于保存执行产物。
    """
    logger.info("mobi.action.request action_type=%s", action_type)
    try:
        try:
            payload_dict = json.loads(payload) if isinstance(payload, str) else payload
        except json.JSONDecodeError:
            payload_dict = {"raw_input": payload}

        task_desc = _build_task_from_action(action_type, payload_dict if isinstance(payload_dict, dict) else {})
        result = _EXECUTOR.run(task=task_desc, output_dir=_resolve_output_dir(output_dir), provider=None)
        metadata = _build_execution_metadata(result.execution)

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "[MobiAgent 操作执行]\n"
                        f"操作类型: {action_type}\n"
                        f"参数: {json.dumps(payload_dict, ensure_ascii=False)}\n"
                        f"结果: {'成功' if result.success else '失败'}\n"
                        f"消息: {result.message}"
                    ),
                )
            ],
            metadata={
                "success": bool(result.success),
                "action_type": action_type,
                **metadata,
            },
        )

    except Exception as exc:  # noqa: BLE001
        mock_result = get_mock_action_result(action_type, payload)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "[MobiAgent Mock 模式 - 操作执行]\n"
                        f"操作类型: {action_type}\n"
                        f"参数: {payload}\n"
                        f"模拟执行结果: {mock_result}\n"
                        f"(实际错误: {str(exc)[:120]})"
                    ),
                )
            ],
            metadata={"mock": True, "result": mock_result},
        )
