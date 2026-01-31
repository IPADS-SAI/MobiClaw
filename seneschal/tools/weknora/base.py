# -*- coding: utf-8 -*-
"""WeKnora API 基础封装。"""

from __future__ import annotations

import json
import uuid
from typing import Any

import requests

from ...config import WEKNORA_CONFIG


def _base_url() -> str:
    """构造 API 基础地址。

    Returns:
        拼接后的 API 基础 URL（包含 /api/v1）。
    """
    base = WEKNORA_CONFIG["base_url"].rstrip("/")
    return f"{base}/api/v1"


def build_headers(api_key: str | None = None, request_id: str | None = None) -> dict:
    """构建通用请求头。

    Args:
        api_key: 覆盖默认 API Key。
        request_id: 自定义请求 ID（用于追踪）。

    Returns:
        包含鉴权与追踪信息的请求头字典。
    """
    return {
        "X-API-Key": api_key or WEKNORA_CONFIG["api_key"],
        "Content-Type": "application/json",
        "X-Request-ID": request_id or uuid.uuid4().hex,
    }


def request_json(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    stream: bool = False,
    timeout: int = 30,
) -> requests.Response:
    """发起 HTTP 请求并返回原始响应。

    Args:
        method: HTTP 方法（GET/POST/PUT/DELETE）。
        path: API 路径（以 / 开头）。
        params: 查询参数。
        json_body: JSON 请求体。
        data: 表单字段。
        files: 文件上传字段。
        headers: 自定义请求头（为空时使用默认头）。
        stream: 是否流式响应。
        timeout: 超时时间（秒）。

    Returns:
        requests.Response 原始响应对象。
    """
    url = f"{_base_url()}{path}"
    return requests.request(
        method=method,
        url=url,
        params=params,
        json=json_body,
        data=data,
        files=files,
        headers=headers or build_headers(),
        stream=stream,
        timeout=timeout,
    )


def parse_json_response(response: requests.Response) -> dict[str, Any]:
    """解析 JSON 响应。

    Args:
        response: requests.Response。

    Returns:
        解析后的 JSON 字典。
    """
    response.raise_for_status()
    return response.json()


def parse_sse_response(response: requests.Response) -> dict[str, Any]:
    """解析 SSE 响应并返回聚合结果。

    Args:
        response: requests.Response（SSE 流式响应）。

    Returns:
        包含 answer、references、events 的聚合字典。
    """
    response.raise_for_status()
    answer_parts: list[str] = []
    thinking_parts: list[str] = []
    references: list = []
    events: list[dict[str, Any]] = []

    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        if not line.startswith("data:"):
            continue

        payload_str = line[len("data:"):].strip()
        if not payload_str:
            continue

        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError:
            continue

        events.append(payload)
        response_type = payload.get("response_type")
        if response_type == "references":
            references.extend(payload.get("knowledge_references") or [])
        elif response_type == "answer":
            answer_parts.append(payload.get("content", ""))
        elif response_type == "thinking":
            thinking_parts.append(payload.get("content", ""))

    return {
        "answer": "".join(answer_parts).strip(),
        "thinking": "".join(thinking_parts).strip(),
        "references": references,
        "events": events,
    }
