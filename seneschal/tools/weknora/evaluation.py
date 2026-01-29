# -*- coding: utf-8 -*-
"""评估 API 封装。"""

from __future__ import annotations

from typing import Any

from .base import request_json, parse_json_response


def get_evaluation(task_id: str) -> dict[str, Any]:
    """获取评估任务结果。

    Args:
        task_id: 评估任务 ID。

    Returns:
        评估结果 JSON。
    """
    response = request_json("GET", "/evaluation", params={"task_id": task_id})
    return parse_json_response(response)


def create_evaluation(payload: dict[str, Any]) -> dict[str, Any]:
    """创建评估任务。

    Args:
        payload: 评估参数。

    Returns:
        创建结果 JSON。
    """
    response = request_json("POST", "/evaluation", json_body=payload)
    return parse_json_response(response)
