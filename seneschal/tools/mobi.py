# -*- coding: utf-8 -*-
"""MobiAgent 工具封装。"""

from __future__ import annotations

import json

import requests

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from ..config import MOBI_AGENT_CONFIG
from .mock_data import get_mock_action_result, get_mock_collect_result


async def call_mobi_collect(task_desc: str) -> ToolResponse:
    """调用 MobiAgent 获取手机端数据（如截图、OCR识别结果等）。"""
    try:
        api_url = f"{MOBI_AGENT_CONFIG['base_url']}/api/v1/collect"
        headers = {
            "Authorization": f"Bearer {MOBI_AGENT_CONFIG['api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "task": task_desc,
            "options": {
                "ocr_enabled": True,
                "timeout": 30,
            },
        }

        response = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()

        collected_data = result.get("data", {})
        ocr_text = collected_data.get("ocr_text", "")
        screenshot_path = collected_data.get("screenshot_path", "")

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"[MobiAgent 收集成功]\n"
                        f"任务: {task_desc}\n"
                        f"截图路径: {screenshot_path}\n"
                        f"OCR识别结果: {ocr_text}"
                    ),
                ),
            ],
            metadata={
                "ocr_text": ocr_text,
                "screenshot_path": screenshot_path,
                "raw_data": collected_data,
            },
        )

    except requests.exceptions.RequestException as e:
        mock_result = get_mock_collect_result(task_desc)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"[MobiAgent Mock 模式]\n"
                        f"任务: {task_desc}\n"
                        f"模拟数据: {mock_result}\n"
                        f"(实际错误: {str(e)[:100]})"
                    ),
                ),
            ],
            metadata={"mock": True, "data": mock_result},
        )
    except Exception as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"[MobiAgent 调用失败] 错误: {str(e)}",
                ),
            ],
        )


async def call_mobi_action(action_type: str, payload: str) -> ToolResponse:
    """指挥 MobiAgent 执行手机端的 GUI 操作。"""
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
