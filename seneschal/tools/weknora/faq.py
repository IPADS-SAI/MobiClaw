# -*- coding: utf-8 -*-
"""FAQ API 封装。"""

from __future__ import annotations

from typing import Any

from .base import request_json, parse_json_response


def list_faq_entries(kb_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """获取 FAQ 条目列表。

    Args:
        kb_id: 知识库 ID。
        params: 查询参数（可选）。

    Returns:
        FAQ 列表 JSON。
    """
    response = request_json(
        "GET",
        f"/knowledge-bases/{kb_id}/faq/entries",
        params=params or {},
    )
    return parse_json_response(response)


def batch_import_faq_entries(kb_id: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """批量导入 FAQ 条目。

    Args:
        kb_id: 知识库 ID。
        entries: FAQ 条目列表。

    Returns:
        导入结果 JSON。
    """
    response = request_json(
        "POST",
        f"/knowledge-bases/{kb_id}/faq/entries",
        json_body={"entries": entries},
    )
    return parse_json_response(response)


def create_faq_entry(kb_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """创建单个 FAQ 条目。

    Args:
        kb_id: 知识库 ID。
        payload: FAQ 条目参数。

    Returns:
        创建结果 JSON。
    """
    response = request_json(
        "POST",
        f"/knowledge-bases/{kb_id}/faq/entry",
        json_body=payload,
    )
    return parse_json_response(response)


def update_faq_entry(kb_id: str, entry_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """更新 FAQ 条目。

    Args:
        kb_id: 知识库 ID。
        entry_id: FAQ 条目 ID。
        payload: 更新参数。

    Returns:
        更新结果 JSON。
    """
    response = request_json(
        "PUT",
        f"/knowledge-bases/{kb_id}/faq/entries/{entry_id}",
        json_body=payload,
    )
    return parse_json_response(response)


def update_faq_status(kb_id: str, entry_ids: list[str], is_enabled: bool) -> dict[str, Any]:
    """批量更新 FAQ 启用状态。

    Args:
        kb_id: 知识库 ID。
        entry_ids: FAQ 条目 ID 列表。
        is_enabled: 是否启用。

    Returns:
        更新结果 JSON。
    """
    response = request_json(
        "PUT",
        f"/knowledge-bases/{kb_id}/faq/entries/status",
        json_body={"entry_ids": entry_ids, "is_enabled": is_enabled},
    )
    return parse_json_response(response)


def update_faq_tags(kb_id: str, entry_ids: list[str], tag_id: str) -> dict[str, Any]:
    """批量更新 FAQ 标签。

    Args:
        kb_id: 知识库 ID。
        entry_ids: FAQ 条目 ID 列表。
        tag_id: 标签 ID。

    Returns:
        更新结果 JSON。
    """
    response = request_json(
        "PUT",
        f"/knowledge-bases/{kb_id}/faq/entries/tags",
        json_body={"entry_ids": entry_ids, "tag_id": tag_id},
    )
    return parse_json_response(response)


def delete_faq_entries(kb_id: str, entry_ids: list[str]) -> dict[str, Any]:
    """批量删除 FAQ 条目。

    Args:
        kb_id: 知识库 ID。
        entry_ids: FAQ 条目 ID 列表。

    Returns:
        删除结果 JSON。
    """
    response = request_json(
        "DELETE",
        f"/knowledge-bases/{kb_id}/faq/entries",
        json_body={"entry_ids": entry_ids},
    )
    return parse_json_response(response)


def search_faq(kb_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """混合搜索 FAQ。

    Args:
        kb_id: 知识库 ID。
        payload: 搜索参数。

    Returns:
        搜索结果 JSON。
    """
    response = request_json(
        "POST",
        f"/knowledge-bases/{kb_id}/faq/search",
        json_body=payload,
    )
    return parse_json_response(response)
