# -*- coding: utf-8 -*-
"""Tools for paper discovery and processing."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import re
import time
import xml.etree.ElementTree as ET

import requests
from agentscope.message import TextBlock
from agentscope.tool import ToolResponse


_ARXIV_API_URL = "https://export.arxiv.org/api/query"
_DBLP_API_URL = "https://dblp.org/search/publ/api"
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _safe_trim_text(value: str | None, max_len: int) -> str:
    text = (value or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _resolve_write_path(path: str) -> tuple[Path | None, str | None]:
    resolved_path = (path or "").strip()
    if not resolved_path:
        return None, "empty_path"

    root = (os.environ.get("SENESCHAL_FILE_WRITE_ROOT") or "").strip()
    target = Path(resolved_path).expanduser()
    if root:
        root_path = Path(root).expanduser().resolve()
        target = target if target.is_absolute() else root_path / target
        try:
            target = target.resolve()
        except FileNotFoundError:
            target = target.absolute()
        if target != root_path and root_path not in target.parents:
            return None, "path_outside_root"
    else:
        target = target.resolve()
    return target, None


def _parse_arxiv_feed(feed_text: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(feed_text)
    except ET.ParseError:
        return []

    entries: list[dict[str, Any]] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title = (entry.findtext(f"{_ATOM_NS}title") or "").strip()
        summary = (entry.findtext(f"{_ATOM_NS}summary") or "").strip()
        published = (entry.findtext(f"{_ATOM_NS}published") or "").strip()
        updated = (entry.findtext(f"{_ATOM_NS}updated") or "").strip()
        entry_id = (entry.findtext(f"{_ATOM_NS}id") or "").strip()
        authors = [
            (author.findtext(f"{_ATOM_NS}name") or "").strip()
            for author in entry.findall(f"{_ATOM_NS}author")
        ]

        pdf_url = ""
        abs_url = ""
        for link in entry.findall(f"{_ATOM_NS}link"):
            href = (link.attrib.get("href") or "").strip()
            if not href:
                continue
            link_type = (link.attrib.get("type") or "").strip()
            link_title = (link.attrib.get("title") or "").strip().lower()
            rel = (link.attrib.get("rel") or "").strip().lower()
            if link_type == "application/pdf" or link_title == "pdf":
                pdf_url = href
            if rel == "alternate":
                abs_url = href

        entries.append(
            {
                "title": title,
                "summary": summary,
                "published": published,
                "updated": updated,
                "id": entry_id,
                "authors": authors,
                "pdf_url": pdf_url,
                "abs_url": abs_url or entry_id,
            }
        )
    return entries


def _normalize_years(years: Any) -> list[int]:
    if years is None:
        return []
    if isinstance(years, int):
        return [years]
    if isinstance(years, (list, tuple, set)):
        values = []
        for item in years:
            if isinstance(item, int):
                values.append(item)
            else:
                try:
                    values.append(int(str(item).strip()))
                except ValueError:
                    continue
        return sorted(set(values))
    if isinstance(years, str):
        text = years.strip()
        if not text:
            return []
        if "-" in text:
            parts = [part.strip() for part in text.split("-") if part.strip()]
            if len(parts) == 2:
                try:
                    start = int(parts[0])
                    end = int(parts[1])
                except ValueError:
                    return []
                if start > end:
                    start, end = end, start
                return list(range(start, end + 1))
        values = []
        for token in re.findall(r"\d{4}", text):
            try:
                values.append(int(token))
            except ValueError:
                continue
        return sorted(set(values))
    return []


def _parse_dblp_hits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    hits = payload.get("result", {}).get("hits", {}).get("hit", [])
    if isinstance(hits, dict):
        hits = [hits]
    entries: list[dict[str, Any]] = []
    for item in hits or []:
        info = item.get("info", {}) if isinstance(item, dict) else {}
        title = (info.get("title") or "").strip()
        year = (info.get("year") or "").strip()
        venue = (info.get("venue") or "").strip()
        url = (info.get("url") or "").strip()
        ee = info.get("ee")
        if isinstance(ee, list):
            ee_list = [entry for entry in ee if isinstance(entry, str)]
        elif isinstance(ee, str):
            ee_list = [ee]
        else:
            ee_list = []

        authors_field = info.get("authors", {}).get("author") if isinstance(info.get("authors"), dict) else None
        if authors_field is None:
            authors_field = info.get("author")
        if isinstance(authors_field, list):
            authors = [str(author).strip() for author in authors_field if str(author).strip()]
        elif isinstance(authors_field, str):
            authors = [authors_field.strip()]
        else:
            authors = []

        entries.append(
            {
                "title": title,
                "authors": authors,
                "year": year,
                "venue": venue,
                "url": url,
                "ee": ee_list,
            }
        )
    return entries


async def arxiv_search(
    query: str,
    max_results: int = 5,
    start: int = 0,
    sort_by: str = "relevance",
    sort_order: str = "descending",
) -> ToolResponse:
    """Search arXiv papers and return metadata.

    Args:
        query: arXiv API query string.
        max_results: Number of results to return.
        start: Offset for pagination.
        sort_by: Sorting field (relevance, lastUpdatedDate, submittedDate).
        sort_order: asc/desc.
    """
    normalized_query = (query or "").strip()
    if not normalized_query:
        return ToolResponse(
            content=[TextBlock(type="text", text="[arXiv] query is required")],
            metadata={"error": "query_missing"},
        )

    timeout_s = float(os.environ.get("SENESCHAL_WEB_TIMEOUT", "15"))
    wanted_results = max(1, min(int(max_results or 5), 50))
    start_index = max(0, int(start or 0))

    params = {
        "search_query": normalized_query,
        "start": start_index,
        "max_results": wanted_results,
        "sortBy": (sort_by or "relevance").strip(),
        "sortOrder": (sort_order or "descending").strip(),
    }

    try:
        resp = requests.get(_ARXIV_API_URL, params=params, timeout=timeout_s)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[arXiv] Request failed: {exc}")],
            metadata={"error": str(exc)},
        )

    entries = _parse_arxiv_feed(resp.text or "")
    if not entries:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[arXiv] No results for query: {normalized_query}")],
            metadata={"query": normalized_query, "result_count": 0},
        )

    lines = [f"[arXiv] query={normalized_query}"]
    for idx, entry in enumerate(entries, start=1):
        title = _safe_trim_text(entry.get("title"), 200)
        summary = _safe_trim_text(entry.get("summary"), 400)
        authors = ", ".join(entry.get("authors") or [])
        lines.append(f"{idx}. {title}")
        if authors:
            lines.append(f"   Authors: {authors}")
        if entry.get("published"):
            lines.append(f"   Published: {entry.get('published')}")
        if entry.get("abs_url"):
            lines.append(f"   URL: {entry.get('abs_url')}")
        if entry.get("pdf_url"):
            lines.append(f"   PDF: {entry.get('pdf_url')}")
        if summary:
            lines.append(f"   Summary: {summary}")

    return ToolResponse(
        content=[TextBlock(type="text", text="\n".join(lines))],
        metadata={"query": normalized_query, "result_count": len(entries), "entries": entries},
    )


async def dblp_conference_search(
    conference: str,
    years: list[int] | str | None = None,
    keyword_query: str | None = None,
    max_results: int = 50,
) -> ToolResponse:
    """Search DBLP for conference papers by name and year hints."""
    normalized_conf = (conference or "").strip()
    if not normalized_conf:
        return ToolResponse(
            content=[TextBlock(type="text", text="[DBLP] conference name is required")],
            metadata={"error": "conference_missing"},
        )

    year_list = _normalize_years(years)
    query_parts = [normalized_conf]
    if year_list:
        query_parts.extend([str(year) for year in year_list])
    if keyword_query:
        query_parts.append(keyword_query.strip())

    base_query = " ".join(part for part in query_parts if part)
    timeout_s = float(os.environ.get("SENESCHAL_WEB_TIMEOUT", "15"))
    wanted_results = max(1, min(int(max_results or 50), 200))

    headers = {
        "User-Agent": "Seneschal/0.1 (+https://github.com/)",
        "Accept": "application/json,text/json;q=0.9,*/*;q=0.1",
    }

    def _request_dblp(query: str) -> tuple[dict[str, Any] | None, str | None]:
        params = {
            "q": query,
            "format": "json",
            "h": wanted_results,
        }
        last_error = None
        for attempt in range(3):
            try:
                resp = requests.get(_DBLP_API_URL, params=params, timeout=timeout_s, headers=headers)
                resp.raise_for_status()
                payload = resp.json() if resp.content else {}
                if isinstance(payload, dict):
                    return payload, None
                return {}, None
            except requests.RequestException as exc:
                last_error = str(exc)
                time.sleep(0.5 * (2 ** attempt))
            except ValueError as exc:
                last_error = f"Invalid JSON response: {exc}"
                break
        return None, last_error

    payload, error = _request_dblp(base_query)
    query = base_query
    if payload is None and keyword_query:
        fallback_parts = [normalized_conf]
        if year_list:
            fallback_parts.extend([str(year) for year in year_list])
        fallback_query = " ".join(part for part in fallback_parts if part)
        payload, error = _request_dblp(fallback_query)
        query = fallback_query
    if payload is None and year_list:
        payload, error = _request_dblp(normalized_conf)
        query = normalized_conf

    if payload is None:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[DBLP] Request failed: {error or 'unknown error'}")],
            metadata={"error": error or "unknown", "query": query},
        )

    entries = _parse_dblp_hits(payload if isinstance(payload, dict) else {})
    if not entries:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[DBLP] No results for query: {query}")],
            metadata={"query": query, "result_count": 0},
        )

    lines = [f"[DBLP] query={query}"]
    for idx, entry in enumerate(entries, start=1):
        title = _safe_trim_text(entry.get("title"), 200)
        authors = ", ".join(entry.get("authors") or [])
        lines.append(f"{idx}. {title}")
        if authors:
            lines.append(f"   Authors: {authors}")
        if entry.get("venue"):
            lines.append(f"   Venue: {entry.get('venue')}")
        if entry.get("year"):
            lines.append(f"   Year: {entry.get('year')}")
        if entry.get("url"):
            lines.append(f"   URL: {entry.get('url')}")
        if entry.get("ee"):
            for link in entry.get("ee") or []:
                lines.append(f"   EE: {link}")

    return ToolResponse(
        content=[TextBlock(type="text", text="\n".join(lines))],
        metadata={"query": query, "result_count": len(entries), "entries": entries},
    )


async def download_file(
    url: str,
    output_path: str,
    max_bytes: int | None = None,
) -> ToolResponse:
    """Download a file to disk with size limits."""
    normalized_url = (url or "").strip()
    if not normalized_url.startswith("http://") and not normalized_url.startswith("https://"):
        return ToolResponse(
            content=[TextBlock(type="text", text="[Download] URL must start with http:// or https://")],
            metadata={"error": "invalid_url"},
        )

    target, error = _resolve_write_path(output_path)
    if error == "empty_path":
        return ToolResponse(
            content=[TextBlock(type="text", text="[Download] Empty output path.")],
            metadata={"error": error},
        )
    if error == "path_outside_root":
        return ToolResponse(
            content=[TextBlock(type="text", text="[Download] Path is outside SENESCHAL_FILE_WRITE_ROOT.")],
            metadata={"error": error},
        )

    timeout_s = float(os.environ.get("SENESCHAL_WEB_TIMEOUT", "15"))
    limit_env = os.environ.get("SENESCHAL_DOWNLOAD_MAX_BYTES", "50000000")
    max_bytes = int(max_bytes) if max_bytes is not None else int(limit_env)

    try:
        resp = requests.get(normalized_url, timeout=timeout_s, stream=True)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Download] Request failed: {exc}")],
            metadata={"error": str(exc)},
        )

    assert target is not None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        bytes_written = 0
        with target.open("wb") as handle:
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                bytes_written += len(chunk)
                if max_bytes and bytes_written > max_bytes:
                    handle.close()
                    try:
                        target.unlink()
                    except FileNotFoundError:
                        pass
                    return ToolResponse(
                        content=[
                            TextBlock(
                                type="text",
                                text=f"[Download] Aborted: file exceeded limit ({max_bytes} bytes).",
                            )
                        ],
                        metadata={"error": "size_limit", "max_bytes": max_bytes},
                    )
                handle.write(chunk)
    except Exception as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Download] Write failed: {exc}")],
            metadata={"error": str(exc)},
        )

    return ToolResponse(
        content=[TextBlock(type="text", text=f"[Download] Wrote: {target}")],
        metadata={"path": str(target), "bytes": bytes_written},
    )


async def extract_pdf_text(
    file_path: str,
    max_pages: int | None = 10,
    max_chars: int | None = None,
) -> ToolResponse:
    """Extract text from a local PDF file."""
    resolved_path = (file_path or "").strip()
    if not resolved_path:
        return ToolResponse(
            content=[TextBlock(type="text", text="[PDF] Empty file path.")],
            metadata={"error": "empty_path"},
        )

    target = Path(resolved_path).expanduser()
    if not target.exists():
        return ToolResponse(
            content=[TextBlock(type="text", text="[PDF] File not found.")],
            metadata={"error": "not_found", "path": str(target)},
        )

    reader_cls = None
    try:
        from pypdf import PdfReader

        reader_cls = PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader

            reader_cls = PdfReader
        except ImportError:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="[PDF] Missing dependency: install pypdf or PyPDF2 to extract PDF text.",
                    )
                ],
                metadata={"error": "missing_dependency"},
            )

    max_chars_env = os.environ.get("SENESCHAL_PDF_MAX_CHARS", "12000")
    max_chars_value = int(max_chars) if max_chars is not None else int(max_chars_env)
    max_chars_value = max(1000, min(max_chars_value, 200000))

    max_pages_value = None
    if max_pages is not None:
        max_pages_value = max(1, min(int(max_pages), 200))

    try:
        reader = reader_cls(str(target))
    except Exception as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[PDF] Failed to read PDF: {exc}")],
            metadata={"error": str(exc)},
        )

    text_parts: list[str] = []
    page_count = len(reader.pages)
    pages_to_read = page_count
    if max_pages_value is not None:
        pages_to_read = min(page_count, max_pages_value)

    for idx in range(pages_to_read):
        try:
            page_text = reader.pages[idx].extract_text() or ""
        except Exception:
            page_text = ""
        if page_text:
            text_parts.append(page_text)
        current_len = sum(len(part) for part in text_parts)
        if current_len >= max_chars_value:
            break

    combined = "\n".join(text_parts)
    if len(combined) > max_chars_value:
        combined = combined[: max_chars_value - 3].rstrip() + "..."

    return ToolResponse(
        content=[TextBlock(type="text", text=f"[PDF] {target}\n{combined}")],
        metadata={"path": str(target), "page_count": page_count, "pages_read": pages_to_read},
    )
