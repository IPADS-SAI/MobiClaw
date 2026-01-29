# -*- coding: utf-8 -*-
"""标签 API 封装。"""

from __future__ import annotations

from typing import Any

from .base import request_json, parse_json_response


def list_tags(kb_id: str, page: int = 1, page_size: int = 20, keyword: str | None = None) -> dict[str, Any]:
    """获取标签列表。

    Args:
        kb_id: 知识库 ID。
        page: 页码。
        page_size: 每页数量。
        keyword: 关键词过滤（可选）。

    Returns:
        标签列表 JSON。
    """
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if keyword:
        params["keyword"] = keyword
    response = request_json("GET", f"/knowledge-bases/{kb_id}/tags", params=params)
    return parse_json_response(response)


def create_tag(kb_id: str, name: str, color: str | None = None, sort_order: int | None = None) -> dict[str, Any]:
    """创建标签。

    Args:
        kb_id: 知识库 ID。
        name: 标签名称。
        color: 标签颜色（可选）。
        sort_order: 排序（可选）。

    Returns:
        创建结果 JSON。
    """
    payload: dict[str, Any] = {"name": name}
    if color:
        payload["color"] = color
    if sort_order is not None:
        payload["sort_order"] = sort_order
    response = request_json("POST", f"/knowledge-bases/{kb_id}/tags", json_body=payload)
    return parse_json_response(response)


def update_tag(kb_id: str, tag_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """更新标签。

    Args:
        kb_id: 知识库 ID。
        tag_id: 标签 ID。
        payload: 更新参数。

    Returns:
        更新结果 JSON。
    """
    response = request_json(
        "PUT",
        f"/knowledge-bases/{kb_id}/tags/{tag_id}",
        json_body=payload,
    )
    return parse_json_response(response)


def delete_tag(kb_id: str, tag_id: str) -> dict[str, Any]:
    """删除标签。

    Args:
        kb_id: 知识库 ID。
        tag_id: 标签 ID。

    Returns:
        删除结果 JSON。
    """
    response = request_json("DELETE", f"/knowledge-bases/{kb_id}/tags/{tag_id}")
    return parse_json_response(response)
