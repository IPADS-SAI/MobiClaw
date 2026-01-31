# -*- coding: utf-8 -*-
"""知识管理 API 封装。"""

from __future__ import annotations

from typing import Any

from .base import request_json, parse_json_response

import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

def create_knowledge_from_file(
    kb_id: str,
    file_path: str,
    metadata: dict[str, Any] | None = None,
    enable_multimodel: bool | None = None,
    file_name: str | None = None,
) -> dict[str, Any]:
    """从文件创建知识。

    Args:
        kb_id: 知识库 ID。
        file_path: 本地文件路径。
        metadata: 元数据（可选）。
        enable_multimodel: 是否启用多模态处理（可选）。
        file_name: 自定义文件名（可选）。

    Returns:
        创建结果 JSON。
    """
    data: dict[str, Any] = {}
    if metadata is not None:
        data["metadata"] = metadata
    if enable_multimodel is not None:
        data["enable_multimodel"] = str(enable_multimodel).lower()
    if file_name:
        data["fileName"] = file_name

    with open(file_path, "rb") as f:
        files = {"file": f}
        response = request_json(
            "POST",
            f"/knowledge-bases/{kb_id}/knowledge/file",
            data=data,
            files=files,
            headers=None,
        )
    return parse_json_response(response)


def create_knowledge_from_url(kb_id: str, url: str, enable_multimodel: bool | None = None) -> dict[str, Any]:
    """从 URL 创建知识。

    Args:
        kb_id: 知识库 ID。
        url: 资源 URL。
        enable_multimodel: 是否启用多模态处理（可选）。

    Returns:
        创建结果 JSON。
    """
    payload: dict[str, Any] = {"url": url}
    if enable_multimodel is not None:
        payload["enable_multimodel"] = enable_multimodel
    response = request_json(
        "POST",
        f"/knowledge-bases/{kb_id}/knowledge/url",
        json_body=payload,
    )
    return parse_json_response(response)


def create_knowledge_manual(
    kb_id: str,
    title: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    status: str | None = None,
    tag_id: str | None = None,
) -> dict[str, Any]:
    """创建手工 Markdown 知识。

    Args:
        kb_id: 知识库 ID。
        title: 标题。
        content: Markdown 内容。
        metadata: 元数据（可选）。

    Returns:
        创建结果 JSON。
    """
    payload: dict[str, Any] = {"title": title, "content": content}
    if metadata is not None:
        payload["metadata"] = metadata
    if status is not None:
        payload["status"] = status
    if tag_id is not None:
        payload["tag_id"] = tag_id
    response = request_json(
        "POST",
        f"/knowledge-bases/{kb_id}/knowledge/manual",
        json_body=payload,
    )
    return parse_json_response(response)


def list_knowledge(kb_id: str, page: int = 1, page_size: int = 20, tag_id: str | None = None) -> dict[str, Any]:
    """获取知识列表。

    Args:
        kb_id: 知识库 ID。
        page: 页码。
        page_size: 每页数量。
        tag_id: 标签 ID（可选）。

    Returns:
        列表结果 JSON。
    """
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if tag_id:
        params["tag_id"] = tag_id
    response = request_json(
        "GET",
        f"/knowledge-bases/{kb_id}/knowledge",
        params=params,
    )
    return parse_json_response(response)


def get_knowledge(knowledge_id: str) -> dict[str, Any]:
    """获取知识详情。

    Args:
        knowledge_id: 知识 ID。

    Returns:
        详情 JSON。
    """
    response = request_json("GET", f"/knowledge/{knowledge_id}")
    return parse_json_response(response)


def delete_knowledge(knowledge_id: str) -> dict[str, Any]:
    """删除知识。

    Args:
        knowledge_id: 知识 ID。

    Returns:
        删除结果 JSON。
    """
    response = request_json("DELETE", f"/knowledge/{knowledge_id}")
    return parse_json_response(response)


def download_knowledge(knowledge_id: str, output_path: str | None = None) -> bytes | str:
    """下载知识文件。

    Args:
        knowledge_id: 知识 ID。
        output_path: 保存路径（可选）。

    Returns:
        若提供 output_path 返回路径，否则返回文件二进制内容。
    """
    response = request_json("GET", f"/knowledge/{knowledge_id}/download", stream=True)
    response.raise_for_status()
    if output_path:
        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return output_path
    return response.content


def update_knowledge(knowledge_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """更新知识。

    Args:
        knowledge_id: 知识 ID。
        payload: 更新参数。

    Returns:
        更新结果 JSON。
    """
    response = request_json("PUT", f"/knowledge/{knowledge_id}", json_body=payload)
    return parse_json_response(response)


def update_manual_knowledge(
    knowledge_id: str,
    title: str | None = None,
    content: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """更新手工 Markdown 知识。

    Args:
        knowledge_id: 知识 ID。
        title: 标题（可选）。
        content: 内容（可选）。
        metadata: 元数据（可选）。

    Returns:
        更新结果 JSON。
    """
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if content is not None:
        payload["content"] = content
    if metadata is not None:
        payload["metadata"] = metadata
    response = request_json(
        "PUT",
        f"/knowledge/manual/{knowledge_id}",
        json_body=payload,
    )
    return parse_json_response(response)


def update_image_chunk(knowledge_id: str, chunk_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """更新图像分块信息。

    Args:
        knowledge_id: 知识 ID。
        chunk_id: 分块 ID。
        payload: 更新参数。

    Returns:
        更新结果 JSON。
    """
    response = request_json(
        "PUT",
        f"/knowledge/image/{knowledge_id}/{chunk_id}",
        json_body=payload,
    )
    return parse_json_response(response)


def update_knowledge_tags(updates: dict[str, str | None]) -> dict[str, Any]:
    """批量更新知识标签。

    Args:
        updates: {knowledge_id: tag_id} 映射，tag_id 可为 None/空表示清空标签。

    Returns:
        更新结果 JSON。
    """
    logger.debug("update_knowledge_tags: %s", updates)

    payload = {"updates": updates}
    response = request_json("PUT", "/knowledge/tags", json_body=payload)
    logger.debug("update_knowledge_tags response: %s", response)
    return parse_json_response(response)


def batch_get_knowledge(ids: list[str]) -> dict[str, Any]:
    """批量获取知识详情。

    Args:
        ids: 知识 ID 列表。

    Returns:
        批量结果 JSON。
    """
    params = [("ids", knowledge_id) for knowledge_id in ids]
    response = request_json("GET", "/knowledge/batch", params=params)
    return parse_json_response(response)
