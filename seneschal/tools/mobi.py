# -*- coding: utf-8 -*-
"""MobiAgent 工具封装。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import requests

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from ..config import MOBI_AGENT_CONFIG
from .mock_data import get_mock_action_result, get_mock_collect_result

logger = logging.getLogger(__name__)


def _load_execution_from_data_dir(data_dir: str) -> dict:
    if not data_dir:
        return {}
    path = Path(data_dir) / "execution_result.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _normalize_collect_result(result: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    success = bool(result.get("success", False))
    message = str(result.get("message", ""))
    data = result.get("data")
    collected_data = data if isinstance(data, dict) else {}
    return success, message, collected_data


def _extract_summary_from_execution(collected_data: dict[str, Any]) -> dict[str, Any]:
    execution = collected_data.get("execution", {})
    if not execution:
        execution = _load_execution_from_data_dir(collected_data.get("data_dir", ""))

    summary = execution.get("summary", {}) if isinstance(execution, dict) else {}
    ocr = execution.get("ocr", {}) if isinstance(execution, dict) else {}
    history = execution.get("history", {}) if isinstance(execution, dict) else {}
    ocr_text = (ocr.get("full_text") if isinstance(ocr, dict) else "") or collected_data.get("ocr_text", "")
    screenshot_path = (summary.get("final_screenshot_path") if isinstance(summary, dict) else "") or collected_data.get("screenshot_path", "")
    reasonings = history.get("reasonings", []) if isinstance(history, dict) else []
    last_reasoning = reasonings[-1] if isinstance(reasonings, list) and reasonings else ""
    action_count = int(summary.get("action_count", 0)) if isinstance(summary, dict) else 0
    step_count = int(summary.get("step_count", 0)) if isinstance(summary, dict) else 0
    status_hint = str(summary.get("status_hint", "")) if isinstance(summary, dict) else ""
    run_dir = str(execution.get("run_dir", "")) if isinstance(execution, dict) else ""
    index_file = str(execution.get("index_file", "")) if isinstance(execution, dict) else ""

    return {
        "execution": execution,
        "ocr_text": ocr_text,
        "screenshot_path": screenshot_path,
        "last_reasoning": last_reasoning,
        "action_count": action_count,
        "step_count": step_count,
        "status_hint": status_hint,
        "run_dir": run_dir,
        "index_file": index_file,
    }


def _collect_request(task_desc: str, timeout: int = 120) -> dict[str, Any]:
    api_url = f"{MOBI_AGENT_CONFIG['base_url']}/api/v1/collect"
    logger.info("mobi.collect.request url=%s task_desc=%s", api_url, (task_desc or "")[:120])
    headers = {
        "Authorization": f"Bearer {MOBI_AGENT_CONFIG['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "task": task_desc,
        "options": {"ocr_enabled": True, "timeout": timeout},
    }
    response = requests.post(api_url, headers=headers, json=payload, timeout=max(timeout + 30, 60))
    response.raise_for_status()
    return response.json()


async def call_mobi_collect(task_desc: str) -> ToolResponse:
    """兼容别名：等价于单次 `call_mobi_collect_verified(max_retries=0)`。"""
    return await call_mobi_collect_verified(task_desc, max_retries=0)


async def call_mobi_collect_verified(task_desc: str, max_retries: int = 2) -> ToolResponse:
    """兼容接口：执行一次收集并返回证据，不做工具内验证或自动重试。"""
    _ = max_retries  # kept for backward compatibility
    try:
        raw = _collect_request(task_desc, timeout=180)
        success, message, collected_data = _normalize_collect_result(raw)
        logger.info("mobi.collect.result success=%s message=%s", success, (message or "")[:120])
        if not success:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            "[MobiAgent 收集失败]\n"
                            f"任务: {task_desc}\n"
                            f"message: {message}\n"
                            f"status: {collected_data.get('status', '')}\n"
                            f"stderr: {(collected_data.get('stderr', '') if isinstance(collected_data, dict) else '')[:500]}"
                        ),
                    )
                ],
                metadata={"success": False, "requires_agent_validation": True, "raw_data": collected_data},
            )
        normalized = _extract_summary_from_execution(collected_data)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "[MobiAgent 收集完成(需Agent验证)]\n"
                        f"任务: {task_desc}\n"
                        f"执行器状态提示: {normalized['status_hint']}\n"
                        f"截图路径: {normalized['screenshot_path']}\n"
                        f"最后推理: {normalized['last_reasoning']}\n"
                        f"OCR摘要: {(normalized['ocr_text'] or '')[:500]}"
                    ),
                )
            ],
            metadata={
                "success": True,
                "requires_agent_validation": True,
                "attempt": 1,
                "original_task": task_desc,
                "final_task": task_desc,
                **normalized,
                "raw_data": collected_data,
            },
        )
    except requests.exceptions.RequestException as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[MobiAgent 调用失败] 请求异常: {exc}")],
            metadata={"success": False, "requires_agent_validation": True, "error": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[MobiAgent 调用失败] 错误: {exc}")],
            metadata={"success": False, "requires_agent_validation": True, "error": str(exc)},
        )


async def call_mobi_action(action_type: str, payload: str) -> ToolResponse:
    """指挥 MobiAgent 执行手机端的 GUI 操作。"""
    logger.info("mobi.action.request action_type=%s", action_type)
    try:
        try:
            payload_dict = json.loads(payload) if isinstance(payload, str) else payload
        except json.JSONDecodeError:
            payload_dict = {"raw_input": payload}

        api_url = f"{MOBI_AGENT_CONFIG['base_url']}/api/v1/action"
        headers = {
            "Authorization": f"Bearer {MOBI_AGENT_CONFIG['api_key']}",
            "Content-Type": "application/json",
        }
        request_payload = {
            "action_type": action_type,
            "params": payload_dict,
            "options": {
                "wait_for_completion": True,
                "timeout": 30,
            },
        }

        response = requests.post(
            api_url,
            headers=headers,
            json=request_payload,
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()

        success = result.get("success", False)
        message = result.get("message", "操作已执行")

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"[MobiAgent 操作执行]\n"
                        f"操作类型: {action_type}\n"
                        f"参数: {json.dumps(payload_dict, ensure_ascii=False)}\n"
                        f"结果: {'成功' if success else '失败'}\n"
                        f"消息: {message}"
                    ),
                ),
            ],
            metadata={"success": success, "action_type": action_type},
        )

    except requests.exceptions.RequestException as e:
        mock_result = get_mock_action_result(action_type, payload)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"[MobiAgent Mock 模式 - 操作执行]\n"
                        f"操作类型: {action_type}\n"
                        f"参数: {payload}\n"
                        f"模拟执行结果: {mock_result}\n"
                        f"(实际错误: {str(e)[:100]})"
                    ),
                ),
            ],
            metadata={"mock": True, "result": mock_result},
        )
    except Exception as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"[MobiAgent 操作执行失败] 错误: {str(e)}",
                ),
            ],
        )
