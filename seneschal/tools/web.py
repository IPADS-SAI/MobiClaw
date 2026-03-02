# -*- coding: utf-8 -*-
"""Simple web fetch tool."""

from __future__ import annotations

import os

import requests
from agentscope.message import TextBlock
from agentscope.tool import ToolResponse


async def fetch_url_text(url: str) -> ToolResponse:
    """Fetch a URL and return trimmed text content."""
    url = (url or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        return ToolResponse(
            content=[TextBlock(type="text", text="[Web] URL must start with http:// or https://")],
        )

    timeout_s = float(os.environ.get("SENESCHAL_WEB_TIMEOUT", "15"))
    max_bytes = int(os.environ.get("SENESCHAL_WEB_MAX_BYTES", "200000"))

    try:
        resp = requests.get(url, timeout=timeout_s, headers={"User-Agent": "Seneschal/0.1"})
        resp.raise_for_status()
    except requests.RequestException as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Web] Request failed: {exc}")],
        )

    content = resp.content[:max_bytes]
    text = content.decode(resp.encoding or "utf-8", errors="replace")

    return ToolResponse(
        content=[TextBlock(type="text", text=f"[Web] {url}\n{text}")],
        metadata={"status_code": resp.status_code, "url": url},
    )
