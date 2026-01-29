# -*- coding: utf-8 -*-
"""聊天 API 封装。"""

from __future__ import annotations

from typing import Any

from .base import request_json, parse_sse_response
from ...config import WEKNORA_CONFIG


def knowledge_chat(session_id: str, query: str, **kwargs: Any) -> dict[str, Any]:
    """基于知识库的问答（SSE）。

    Args:
        session_id: 会话 ID。
        query: 查询文本。
        **kwargs: 其他接口参数。

    Returns:
        聚合后的 SSE 结果（answer/references/events）。
    """
    payload = {"query": query, **kwargs}
    response = request_json(
        "POST",
        f"/knowledge-chat/{session_id}",
        json_body=payload,
        stream=True,
        timeout=60,
    )
    return parse_sse_response(response)


def agent_chat(session_id: str, query: str, **kwargs: Any) -> dict[str, Any]:
    """基于 Agent 的智能问答（SSE）。

    Args:
        session_id: 会话 ID。
        query: 查询文本。
        **kwargs: 其他接口参数。

    Returns:
        聚合后的 SSE 结果（answer/references/events）。
    """
    payload = {"query": query, **kwargs}
    if "knowledge_base_ids" not in payload and WEKNORA_CONFIG.get("knowledge_base_id"):
        payload["knowledge_base_ids"] = [WEKNORA_CONFIG["knowledge_base_id"]]
    response = request_json(
        "POST",
        f"/agent-chat/{session_id}",
        json_body=payload,
        stream=True,
        timeout=60,
    )
    return parse_sse_response(response)
