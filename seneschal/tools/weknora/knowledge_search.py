# -*- coding: utf-8 -*-
"""知识搜索 API 封装。"""

from __future__ import annotations

from typing import Any

from .base import request_json, parse_json_response
from ...config import WEKNORA_CONFIG


def knowledge_search(
    query: str,
    knowledge_base_id: str | None = None,
    knowledge_base_ids: list[str] | None = None,
    knowledge_ids: list[str] | None = None,
) -> dict[str, Any]:
    """在知识库中搜索内容（不使用 LLM 总结）。

    Args:
        query: 搜索查询文本。
        knowledge_base_id: 单个知识库 ID（向后兼容）。
        knowledge_base_ids: 知识库 ID 列表。
        knowledge_ids: 指定知识文件 ID 列表。

    Returns:
        搜索结果 JSON。
    """
    payload: dict[str, Any] = {"query": query}
    if knowledge_base_ids:
        payload["knowledge_base_ids"] = knowledge_base_ids
    elif knowledge_base_id:
        payload["knowledge_base_id"] = knowledge_base_id
    else:
        payload["knowledge_base_ids"] = [WEKNORA_CONFIG["knowledge_base_id"]]

    if knowledge_ids:
        payload["knowledge_ids"] = knowledge_ids

    response = request_json("POST", "/knowledge-search", json_body=payload)
    return parse_json_response(response)
