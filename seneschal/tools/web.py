# -*- coding: utf-8 -*-
"""Simple web fetch tool."""

from __future__ import annotations

import html
import os
import re

from urllib.parse import urljoin, urlparse

import requests
from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from ..config import BRAVE_SEARCH_CONFIG


def _fetch_url_text(url: str) -> tuple[str, int] | tuple[None, int]:
    url = (url or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        return None, 0

    timeout_s = float(os.environ.get("SENESCHAL_WEB_TIMEOUT", "15"))
    max_bytes = int(os.environ.get("SENESCHAL_WEB_MAX_BYTES", "200000"))

    resp = requests.get(url, timeout=timeout_s, headers={"User-Agent": "Seneschal/0.1"})
    resp.raise_for_status()
    content = resp.content[:max_bytes]
    text = content.decode(resp.encoding or "utf-8", errors="replace")
    return text, resp.status_code


def _safe_trim_text(value: str | None, max_len: int) -> str:
    text = (value or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _format_brave_results(results: list[dict[str, str]], query: str) -> str:
    if not results:
        return f"[BraveSearch] query={query}\nNo results found."

    lines = [f"[BraveSearch] query={query}"]
    for idx, item in enumerate(results, start=1):
        title = item.get("title") or "(no title)"
        url = item.get("url") or ""
        desc = item.get("description") or ""
        lines.append(f"{idx}. {title}")
        lines.append(f"   URL: {url}")
        if desc:
            lines.append(f"   Snippet: {desc}")
    return "\n".join(lines)


def _brave_search_request(query: str, max_results: int) -> tuple[list[dict[str, str]], int]:
    api_key = BRAVE_SEARCH_CONFIG.get("api_key", "").strip()
    if not api_key:
        raise ValueError("BRAVE_API_KEY is not configured")

    timeout_s = float(os.environ.get("SENESCHAL_WEB_TIMEOUT", "15"))
    base_url = BRAVE_SEARCH_CONFIG.get("base_url", "https://api.search.brave.com/res/v1/web/search")

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
        "User-Agent": "Seneschal/0.1",
    }
    params = {
        "q": query,
        "count": max_results,
    }

    resp = requests.get(base_url, headers=headers, params=params, timeout=timeout_s)
    resp.raise_for_status()
    payload = resp.json() if resp.content else {}

    web_payload = payload.get("web") if isinstance(payload, dict) else {}
    raw_items = web_payload.get("results") if isinstance(web_payload, dict) else []

    formatted_items: list[dict[str, str]] = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        if not url:
            continue
        formatted_items.append(
            {
                "title": _safe_trim_text(item.get("title"), 180),
                "url": url,
                "description": _safe_trim_text(item.get("description"), 360),
            }
        )

    return formatted_items, resp.status_code


def _strip_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    cleaned = re.sub(r"<script[\s\S]*?</script>", " ", raw_html, flags=re.IGNORECASE)
    cleaned = re.sub(r"<style[\s\S]*?</style>", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _select_main_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    patterns = [
        r"<article[^>]*>[\s\S]*?</article>",
        r"<main[^>]*>[\s\S]*?</main>",
        r"<div[^>]+id=[\"']?(content|main|article|post|entry|body)[^>]*>[\s\S]*?</div>",
        r"<div[^>]+class=[\"'][^\"']*(content|main|article|post|entry|body)[^\"']*[^>]*>[\s\S]*?</div>",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_html, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return raw_html


def _is_noise_link(url: str) -> bool:
    lowered = url.lower()
    if lowered.startswith("mailto:") or lowered.startswith("javascript:") or lowered.startswith("tel:"):
        return True
    blocked_keywords = [
        "login",
        "signin",
        "signup",
        "register",
        "auth",
        "oauth",
        "logout",
        "account",
        "user",
        "subscribe",
        "ads",
        "advert",
        "promo",
        "banner",
        "adservice",
        "doubleclick",
    ]
    return any(keyword in lowered for keyword in blocked_keywords)


def _extract_links(base_url: str, raw_html: str, max_links: int) -> list[str]:
    if not raw_html:
        return []
    links: list[str] = []
    seen: set[str] = set()
    content_html = _select_main_html(raw_html)
    for match in re.finditer(r'href=["\"]([^"\"]+)["\"]', content_html, flags=re.IGNORECASE):
        href = match.group(1).strip()
        if not href or href.startswith("#"):
            continue
        resolved = urljoin(base_url, href)
        if not resolved.startswith("http://") and not resolved.startswith("https://"):
            continue
        if _is_noise_link(resolved):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        links.append(resolved)
        if len(links) >= max_links:
            break
    return links


async def fetch_url_text(url: str) -> ToolResponse:
    """Fetch a URL and return trimmed raw text content."""
    url = (url or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        return ToolResponse(
            content=[TextBlock(type="text", text="[Web] URL must start with http:// or https://")],
        )

    try:
        text, status_code = _fetch_url_text(url)
    except requests.RequestException as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Web] Request failed: {exc}")],
        )

    return ToolResponse(
        content=[TextBlock(type="text", text=f"[Web] {url}\n{text}")],
        metadata={"status_code": status_code, "url": url},
    )


async def fetch_url_readable_text(url: str) -> ToolResponse:
    """Fetch a URL and return HTML-stripped readable text content."""
    url = (url or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        return ToolResponse(
            content=[TextBlock(type="text", text="[Web] URL must start with http:// or https://")],
        )

    try:
        text, status_code = _fetch_url_text(url)
    except requests.RequestException as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Web] Request failed: {exc}")],
        )

    readable = _strip_html(text or "")
    return ToolResponse(
        content=[TextBlock(type="text", text=f"[Web] {url}\n{readable}")],
        metadata={"status_code": status_code, "url": url},
    )


async def fetch_url_links(url: str, max_links: int = 20, same_domain_only: bool = False) -> ToolResponse:
    """Fetch a URL and return extracted links."""
    url = (url or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        return ToolResponse(
            content=[TextBlock(type="text", text="[Web] URL must start with http:// or https://")],
        )

    try:
        text, status_code = _fetch_url_text(url)
    except requests.RequestException as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Web] Request failed: {exc}")],
        )

    max_links = max(1, min(int(max_links or 20), 100))
    links = _extract_links(url, text or "", max_links)
    if same_domain_only:
        base_netloc = urlparse(url).netloc
        links = [link for link in links if urlparse(link).netloc == base_netloc]

    joined = "\n".join(links)
    return ToolResponse(
        content=[TextBlock(type="text", text=f"[Web] {url}\n{joined}")],
        metadata={"status_code": status_code, "url": url, "link_count": len(links)},
    )


async def brave_search(query: str, max_results: int | None = None) -> ToolResponse:
    """Search the web via Brave Search API and return concise result snippets."""
    normalized_query = (query or "").strip()
    if not normalized_query:
        return ToolResponse(
            content=[TextBlock(type="text", text="[BraveSearch] query is required")],
            metadata={"provider": "brave"},
        )

    default_results = int(BRAVE_SEARCH_CONFIG.get("max_results", 5) or 5)
    wanted_results = max_results if max_results is not None else default_results
    wanted_results = max(1, min(int(wanted_results), 20))

    try:
        results, status_code = _brave_search_request(normalized_query, wanted_results)
    except ValueError as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[BraveSearch] {exc}")],
            metadata={"provider": "brave", "query": normalized_query, "error": str(exc)},
        )
    except requests.RequestException as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[BraveSearch] Request failed: {exc}")],
            metadata={"provider": "brave", "query": normalized_query, "error": str(exc)},
        )
    except (TypeError, ValueError) as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[BraveSearch] Failed to parse response: {exc}")],
            metadata={"provider": "brave", "query": normalized_query, "error": str(exc)},
        )

    text = _format_brave_results(results, normalized_query)
    return ToolResponse(
        content=[TextBlock(type="text", text=text)],
        metadata={
            "provider": "brave",
            "query": normalized_query,
            "count": len(results),
            "status_code": status_code,
            "results": results,
        },
    )
