# -*- coding: utf-8 -*-
"""知识库管理 API 封装。"""

from __future__ import annotations

from typing import Any

import importlib

_base = importlib.import_module("seneschal.tools.weknora.base")
request_json = _base.request_json
parse_json_response = _base.parse_json_response


def create_knowledge_base(payload: dict[str, Any]) -> dict[str, Any]:
    """创建知识库。

    Args:
        payload: 知识库创建参数。

    Returns:
        创建结果 JSON。
    """
    response = request_json("POST", "/knowledge-bases", json_body=payload)
    return parse_json_response(response)


def list_knowledge_bases() -> dict[str, Any]:
    """获取知识库列表。

    Returns:
        知识库列表 JSON。
    """
    response = request_json("GET", "/knowledge-bases")
    return parse_json_response(response)


def get_knowledge_base(kb_id: str) -> dict[str, Any]:
    """获取知识库详情。

    Args:
        kb_id: 知识库 ID。

    Returns:
        知识库详情 JSON。
    """
    response = request_json("GET", f"/knowledge-bases/{kb_id}")
    return parse_json_response(response)


def update_knowledge_base(kb_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """更新知识库。

    Args:
        kb_id: 知识库 ID。
        payload: 更新参数。

    Returns:
        更新结果 JSON。
    """
    response = request_json("PUT", f"/knowledge-bases/{kb_id}", json_body=payload)
    return parse_json_response(response)


def delete_knowledge_base(kb_id: str) -> dict[str, Any]:
    """删除知识库。

    Args:
        kb_id: 知识库 ID。

    Returns:
        删除结果 JSON。
    """
    response = request_json("DELETE", f"/knowledge-bases/{kb_id}")
    return parse_json_response(response)


def copy_knowledge_base(kb_id: str, name: str | None = None, description: str | None = None) -> dict[str, Any]:
    """复制知识库。

    Args:
        kb_id: 被复制的知识库 ID。
        name: 新知识库名称（可选）。
        description: 新知识库描述（可选）。

    Returns:
        复制结果 JSON。
    """
    payload: dict[str, Any] = {"id": kb_id}
    if name:
        payload["name"] = name
    if description:
        payload["description"] = description
    response = request_json("POST", "/knowledge-bases/copy", json_body=payload)
    return parse_json_response(response)


def hybrid_search(kb_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """混合搜索（向量+关键词）。

    Args:
        kb_id: 知识库 ID。
        payload: 查询参数。

    Returns:
        检索结果 JSON。
    """
    response = request_json(
        "GET",
        f"/knowledge-bases/{kb_id}/hybrid-search",
        params=payload,
    )
    return parse_json_response(response)
