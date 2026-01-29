# -*- coding: utf-8 -*-
"""智能体 API 封装。"""

from __future__ import annotations

from typing import Any

from .base import request_json, parse_json_response


def create_agent(payload: dict[str, Any]) -> dict[str, Any]:
    """创建智能体。

    Args:
        payload: 智能体创建参数。

    Returns:
        创建结果 JSON。
    """
    response = request_json("POST", "/agents", json_body=payload)
    return parse_json_response(response)


def list_agents() -> dict[str, Any]:
    """获取智能体列表。

    Returns:
        智能体列表 JSON。
    """
    response = request_json("GET", "/agents")
    return parse_json_response(response)


def get_agent(agent_id: str) -> dict[str, Any]:
    """获取智能体详情。

    Args:
        agent_id: 智能体 ID。

    Returns:
        智能体详情 JSON。
    """
    response = request_json("GET", f"/agents/{agent_id}")
    return parse_json_response(response)


def update_agent(agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """更新智能体。

    Args:
        agent_id: 智能体 ID。
        payload: 更新参数。

    Returns:
        更新结果 JSON。
    """
    response = request_json("PUT", f"/agents/{agent_id}", json_body=payload)
    return parse_json_response(response)


def delete_agent(agent_id: str) -> dict[str, Any]:
    """删除智能体。

    Args:
        agent_id: 智能体 ID。

    Returns:
        删除结果 JSON。
    """
    response = request_json("DELETE", f"/agents/{agent_id}")
    return parse_json_response(response)


def copy_agent(agent_id: str, name: str | None = None, description: str | None = None) -> dict[str, Any]:
    """复制智能体。

    Args:
        agent_id: 被复制的智能体 ID。
        name: 新名称（可选）。
        description: 新描述（可选）。

    Returns:
        复制结果 JSON。
    """
    payload: dict[str, Any] = {"id": agent_id}
    if name:
        payload["name"] = name
    if description:
        payload["description"] = description
    response = request_json("POST", f"/agents/{agent_id}/copy", json_body=payload)
    return parse_json_response(response)


def list_agent_placeholders() -> dict[str, Any]:
    """获取智能体占位符定义。

    Returns:
        占位符定义 JSON。
    """
    response = request_json("GET", "/agents/placeholders")
    return parse_json_response(response)
