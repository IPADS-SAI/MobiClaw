# -*- coding: utf-8 -*-
"""模型管理 API 封装。"""

from __future__ import annotations

from typing import Any

from .base import request_json, parse_json_response


def create_model(payload: dict[str, Any]) -> dict[str, Any]:
    """创建模型配置。

    Args:
        payload: 模型创建参数。

    Returns:
        创建结果 JSON。
    """
    response = request_json("POST", "/models", json_body=payload)
    return parse_json_response(response)


def list_models(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """获取模型列表。

    Args:
        params: 查询参数（可选）。

    Returns:
        模型列表 JSON。
    """
    response = request_json("GET", "/models", params=params or {})
    return parse_json_response(response)


def get_model(model_id: str) -> dict[str, Any]:
    """获取模型详情。

    Args:
        model_id: 模型 ID。

    Returns:
        模型详情 JSON。
    """
    response = request_json("GET", f"/models/{model_id}")
    return parse_json_response(response)


def update_model(model_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """更新模型配置。

    Args:
        model_id: 模型 ID。
        payload: 更新参数。

    Returns:
        更新结果 JSON。
    """
    response = request_json("PUT", f"/models/{model_id}", json_body=payload)
    return parse_json_response(response)


def delete_model(model_id: str) -> dict[str, Any]:
    """删除模型配置。

    Args:
        model_id: 模型 ID。

    Returns:
        删除结果 JSON。
    """
    response = request_json("DELETE", f"/models/{model_id}")
    return parse_json_response(response)


def list_model_providers(model_type: str | None = None) -> dict[str, Any]:
    """获取模型服务商列表。

    Args:
        model_type: 模型类型过滤（可选）。

    Returns:
        服务商列表 JSON。
    """
    params = {"model_type": model_type} if model_type else None
    response = request_json("GET", "/models/providers", params=params)
    return parse_json_response(response)
