# -*- coding: utf-8 -*-
"""会话 API 封装。"""

from __future__ import annotations

from typing import Any

from .base import request_json, parse_json_response


def create_session(payload: dict[str, Any]) -> dict[str, Any]:
    """创建会话。

    Args:
        payload: 会话创建参数。

    Returns:
        创建结果 JSON。
    """
    response = request_json("POST", "/sessions", json_body=payload)
    return parse_json_response(response)


def get_session(session_id: str) -> dict[str, Any]:
    """获取会话详情。

    Args:
        session_id: 会话 ID。

    Returns:
        会话详情 JSON。
    """
    response = request_json("GET", f"/sessions/{session_id}")
    return parse_json_response(response)


def list_sessions(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """获取会话列表。

    Args:
        params: 查询参数（可选）。

    Returns:
        会话列表 JSON。
    """
    response = request_json("GET", "/sessions", params=params or {})
    return parse_json_response(response)


def update_session(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """更新会话。

    Args:
        session_id: 会话 ID。
        payload: 更新参数。

    Returns:
        更新结果 JSON。
    """
    response = request_json("PUT", f"/sessions/{session_id}", json_body=payload)
    return parse_json_response(response)


def delete_session(session_id: str) -> dict[str, Any]:
    """删除会话。

    Args:
        session_id: 会话 ID。

    Returns:
        删除结果 JSON。
    """
    response = request_json("DELETE", f"/sessions/{session_id}")
    return parse_json_response(response)


def generate_session_title(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """生成会话标题。

    Args:
        session_id: 会话 ID。
        payload: 生成参数。

    Returns:
        生成结果 JSON。
    """
    response = request_json(
        "POST",
        f"/sessions/{session_id}/generate_title",
        json_body=payload,
    )
    return parse_json_response(response)


def continue_stream(session_id: str) -> dict[str, Any]:
    """继续会话的流式输出。

    Args:
        session_id: 会话 ID。

    Returns:
        继续结果 JSON。
    """
    response = request_json("GET", f"/sessions/continue-stream/{session_id}")
    return parse_json_response(response)
