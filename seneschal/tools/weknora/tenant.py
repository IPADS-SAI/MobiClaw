# -*- coding: utf-8 -*-
"""租户 API 封装。"""

from __future__ import annotations

from typing import Any

from .base import request_json, parse_json_response


def create_tenant(payload: dict[str, Any]) -> dict[str, Any]:
    """创建租户。

    Args:
        payload: 租户创建参数。

    Returns:
        创建结果 JSON。
    """
    response = request_json("POST", "/tenants", json_body=payload)
    return parse_json_response(response)


def get_tenant(tenant_id: str | int) -> dict[str, Any]:
    """获取租户详情。

    Args:
        tenant_id: 租户 ID。

    Returns:
        租户详情 JSON。
    """
    response = request_json("GET", f"/tenants/{tenant_id}")
    return parse_json_response(response)


def update_tenant(tenant_id: str | int, payload: dict[str, Any]) -> dict[str, Any]:
    """更新租户。

    Args:
        tenant_id: 租户 ID。
        payload: 更新参数。

    Returns:
        更新结果 JSON。
    """
    response = request_json("PUT", f"/tenants/{tenant_id}", json_body=payload)
    return parse_json_response(response)


def delete_tenant(tenant_id: str | int) -> dict[str, Any]:
    """删除租户。

    Args:
        tenant_id: 租户 ID。

    Returns:
        删除结果 JSON。
    """
    response = request_json("DELETE", f"/tenants/{tenant_id}")
    return parse_json_response(response)


def list_tenants() -> dict[str, Any]:
    """获取租户列表。

    Returns:
        租户列表 JSON。
    """
    response = request_json("GET", "/tenants")
    return parse_json_response(response)
