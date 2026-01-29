# -*- coding: utf-8 -*-
"""消息 API 封装。"""

from __future__ import annotations

from typing import Any

from .base import request_json, parse_json_response


def load_messages(session_id: str, before_time: str | None = None, limit: int | None = None) -> dict[str, Any]:
    """获取最近的会话消息列表。

    Args:
        session_id: 会话 ID。
        before_time: 上次拉取的最早消息时间（可选）。
        limit: 拉取条数（可选）。

    Returns:
        消息列表 JSON。
    """
    params: dict[str, Any] = {}
    if before_time:
        params["before_time"] = before_time
    if limit is not None:
        params["limit"] = limit
    response = request_json("GET", f"/messages/{session_id}/load", params=params)
    return parse_json_response(response)


def delete_message(session_id: str, message_id: str) -> dict[str, Any]:
    """删除指定消息。

    Args:
        session_id: 会话 ID。
        message_id: 消息 ID。

    Returns:
        删除结果 JSON。
    """
    response = request_json("DELETE", f"/messages/{session_id}/{message_id}")
    return parse_json_response(response)
