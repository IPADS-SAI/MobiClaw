# -*- coding: utf-8 -*-
"""分块 API 封装。"""

from __future__ import annotations

from typing import Any

from .base import request_json, parse_json_response


def list_chunks(knowledge_id: str, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """获取知识分块列表。

    Args:
        knowledge_id: 知识 ID。
        page: 页码。
        page_size: 每页数量。

    Returns:
        分块列表 JSON。
    """
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    response = request_json("GET", f"/chunks/{knowledge_id}", params=params)
    return parse_json_response(response)


def delete_chunk(knowledge_id: str, chunk_id: str) -> dict[str, Any]:
    """删除指定分块。

    Args:
        knowledge_id: 知识 ID。
        chunk_id: 分块 ID。

    Returns:
        删除结果 JSON。
    """
    response = request_json("DELETE", f"/chunks/{knowledge_id}/{chunk_id}")
    return parse_json_response(response)


def delete_all_chunks(knowledge_id: str) -> dict[str, Any]:
    """删除知识下的所有分块。

    Args:
        knowledge_id: 知识 ID。

    Returns:
        删除结果 JSON。
    """
    response = request_json("DELETE", f"/chunks/{knowledge_id}")
    return parse_json_response(response)
