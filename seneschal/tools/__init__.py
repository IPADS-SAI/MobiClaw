# -*- coding: utf-8 -*-
"""Seneschal 工具包。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse
import requests

from .mobi import call_mobi_action, call_mobi_collect, call_mobi_collect_verified
from .file import write_text_file
from .papers import arxiv_search, dblp_conference_search, download_file, extract_pdf_text
from .office import (
    create_docx_from_text,
    create_pdf_from_text,
    edit_docx,
    read_docx_text,
    read_xlsx_summary,
    write_xlsx_from_records,
    write_xlsx_from_rows,
)
from .ppt import (
    create_pptx_from_outline,
    edit_pptx,
    insert_pptx_image,
    read_pptx_summary,
    set_pptx_text_style,
)
from .skill_runner import run_skill_script
from .shell import run_shell_command
from .web import brave_search, fetch_url_links, fetch_url_readable_text, fetch_url_text
from .weknora import (
    knowledge_chat,
    agent_chat,
    knowledge_search,
    create_knowledge_manual,
    list_knowledge_bases,
    list_agents,
    create_session,
    list_tags,
    create_tag,
    update_knowledge_tags,
    update_knowledge,
)
from ..config import WEKNORA_CONFIG

logger = logging.getLogger(__name__)

import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

_KB_ID_CACHE: dict[str, str] = {}
_KB_INFO_CACHE: dict[str, dict[str, Any]] = {}
_AGENT_ID_CACHE: dict[str, str] = {}
_SESSION_ID: str | None = None
_CACHE_PATH = Path(__file__).with_name("weknora_cache.json")


def _load_cache() -> None:
    global _SESSION_ID
    if not _CACHE_PATH.exists():
        return
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return

    kb_cache = data.get("knowledge_bases") or {}
    if isinstance(kb_cache, dict):
        for name, info in kb_cache.items():
            if isinstance(info, dict) and info.get("id"):
                _KB_INFO_CACHE[name] = info
                _KB_ID_CACHE[name] = info["id"]

    agent_cache = data.get("agents") or {}
    if isinstance(agent_cache, dict):
        for name, agent_id in agent_cache.items():
            if agent_id:
                _AGENT_ID_CACHE[name] = agent_id

    session_id = data.get("session_id")
    if session_id:
        _SESSION_ID = session_id


def _save_cache() -> None:
    payload = {
        "knowledge_bases": _KB_INFO_CACHE,
        "agents": _AGENT_ID_CACHE,
        "session_id": _SESSION_ID,
    }
    _CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


_load_cache()


def _resolve_kb_id(kb_id: str | None = None, kb_name: str | None = None) -> str | None:
    """Resolve knowledge base id by explicit id or configured name."""
    if kb_id:
        return kb_id

    name = kb_name or WEKNORA_CONFIG.get("knowledge_base_name") or ""
    if not name:
        return WEKNORA_CONFIG.get("knowledge_base_id")

    if name in _KB_ID_CACHE:
        return _KB_ID_CACHE[name]

    try:
        resp = list_knowledge_bases()
    except Exception:
        logger.exception("Failed to list knowledge bases for kb name resolution")
        return WEKNORA_CONFIG.get("knowledge_base_id")

    bases = []
    if isinstance(resp, dict):
        bases = resp.get("data") or resp.get("knowledge_bases") or []
    for item in bases:
        if item.get("name") == name:
            kb_id = item.get("id")
            if kb_id:
                _KB_ID_CACHE[name] = kb_id
                return kb_id
    return WEKNORA_CONFIG.get("knowledge_base_id")


def _resolve_kb_info(kb_id: str | None = None, kb_name: str | None = None) -> dict[str, Any] | None:
    """Resolve knowledge base info by id or name."""
    if kb_id:
        return {"id": kb_id, "name": kb_name or "", "kb_type": "document"}

    name = kb_name or WEKNORA_CONFIG.get("knowledge_base_name") or ""
    if not name:
        fallback_id = WEKNORA_CONFIG.get("knowledge_base_id")
        if fallback_id:
            return {"id": fallback_id, "name": "", "kb_type": "document"}
        return None

    if name in _KB_INFO_CACHE:
        return _KB_INFO_CACHE[name]

    try:
        resp = list_knowledge_bases()
    except Exception:
        logger.exception("Failed to list knowledge bases for kb info resolution")
        fallback_id = WEKNORA_CONFIG.get("knowledge_base_id")
        if fallback_id:
            return {"id": fallback_id, "name": name, "kb_type": "document"}
        return None

    bases = []
    if isinstance(resp, dict):
        bases = resp.get("data") or resp.get("knowledge_bases") or []
    for item in bases:
        if item.get("name") == name:
            info = {
                "id": item.get("id"),
                "name": item.get("name"),
                "kb_type": item.get("type", "document"),
            }
            if info["id"]:
                _KB_INFO_CACHE[name] = info
                _KB_ID_CACHE[name] = info["id"]
                _save_cache()
                return info
    return None


def _resolve_agent_id(agent_id: str | None = None, agent_name: str | None = None) -> str | None:
    """Resolve agent id by explicit id or configured name."""
    if agent_id:
        return agent_id

    name = agent_name or WEKNORA_CONFIG.get("agent_name") or ""
    if not name:
        return None

    if name in _AGENT_ID_CACHE:
        return _AGENT_ID_CACHE[name]

    try:
        resp = list_agents()
    except Exception:
        logger.exception("Failed to list agents for agent name resolution")
        return None

    agents = []
    if isinstance(resp, dict):
        agents = resp.get("data") or resp.get("agents") or []
    for item in agents:
        if item.get("name") == name:
            resolved_id = item.get("id")
            if resolved_id:
                _AGENT_ID_CACHE[name] = resolved_id
                _save_cache()
                return resolved_id
    return None


def _resolve_tag_id(kb_id: str, tag_name: str) -> str | None:
    """Resolve tag id by name; create if missing."""
    normalized_name = (tag_name or "").strip()

    def _find_tag_in_response(resp: dict[str, Any]) -> str | None:
        data_field = resp.get("data")
        tag_list = None
        if isinstance(data_field, list):
            tag_list = data_field
        elif isinstance(data_field, dict):
            tag_list = data_field.get("data")
        if isinstance(tag_list, list):
            for item in tag_list:
                if not isinstance(item, dict):
                    continue
                if item.get("name") == normalized_name:
                    return item.get("id")
        return None

    try:
        resp = list_tags(kb_id, page=1, page_size=50, keyword=normalized_name)
    except Exception:
        logger.exception("Failed to list tags for tag resolution")
        resp = None
    if isinstance(resp, dict):
        found = _find_tag_in_response(resp)
        if found:
            return found
    # fallback: list without keyword to avoid filtering mismatches
    try:
        resp = list_tags(kb_id, page=1, page_size=50, keyword=None)
        if isinstance(resp, dict):
            found = _find_tag_in_response(resp)
            if found:
                return found
    except Exception:
        logger.exception("Failed to list tags without keyword for tag resolution")
    try:
        created = create_tag(kb_id, normalized_name)
    except requests.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", None)
        if status_code == 409:
            # Tag already exists, re-fetch to get ID.
            try:
                resp = list_tags(kb_id, page=1, page_size=50, keyword=normalized_name)
                if isinstance(resp, dict):
                    found = _find_tag_in_response(resp)
                    if found:
                        return found
                resp = list_tags(kb_id, page=1, page_size=50, keyword=None)
                if isinstance(resp, dict):
                    found = _find_tag_in_response(resp)
                    if found:
                        return found
            except Exception:
                logger.exception("Failed to re-fetch tag after conflict for %s", tag_name)
            return None
        logger.exception("Failed to create tag for %s", tag_name)
        return None
    except Exception:
        logger.exception("Failed to create tag for %s", tag_name)
        return None
    if isinstance(created, dict):
        data = created.get("data") if isinstance(created.get("data"), dict) else created
        if isinstance(data, dict):
            return data.get("id")
    return None


def weknora_add_knowledge(
    content: str | dict[str, Any] | list[Any],
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
    status: str | None = "publish",
    tag_id: str | None = None,
    kb_id: str | None = None,
    kb_name: str | None = None,
) -> ToolResponse:
    """将内容写入 WeKnora 知识库（手工知识）。

    Args:
        content: 需要存储的内容文本或结构化数据。
        title: 知识标题（可选）。
        metadata: 元数据（可选）。
        kb_id: 知识库 ID（可选，默认取配置）。

    Returns:
        ToolResponse，包含创建结果。
    """
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)

    resolved_title = title or "Seneschal 记录"
    resolved_kb_id = _resolve_kb_id(kb_id, kb_name)
    if not resolved_kb_id:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="[WeKnora] 未找到可用的知识库 ID，请检查 WEKNORA_KB_NAME 或 WEKNORA_KB_ID。",
                ),
            ],
            metadata={"error": "kb_id_missing"},
        )
    normalized_status = status.lower().strip() if isinstance(status, str) else status
    logger.info("WeKnora add knowledge: kb_id=%s title=%s", resolved_kb_id, resolved_title)
    result = create_knowledge_manual(
        resolved_kb_id,
        title=resolved_title,
        content=content,
        metadata=metadata,
        status=normalized_status,
        tag_id=tag_id,
    )
    knowledge_id = None
    if isinstance(result, dict):
        data = result.get("data", result)
        if isinstance(data, dict):
            knowledge_id = data.get("id")
        elif isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                knowledge_id = first.get("id")
    if not knowledge_id:
        logger.warning("WeKnora add knowledge: missing knowledge_id in response: %s", result)
    if knowledge_id:
        if not tag_id:
            date_tag = datetime.now().strftime("%Y-%m-%d")
            resolved_tag_id = _resolve_tag_id(resolved_kb_id, date_tag)
            if resolved_tag_id:
                try:
                    update_knowledge_tags({knowledge_id: resolved_tag_id})
                    logger.info("Tagged knowledge %s with tag %s", knowledge_id, resolved_tag_id)
                except Exception:
                    logger.exception("Failed to tag knowledge %s", knowledge_id)
                    try:
                        update_knowledge(knowledge_id, {"tag_id": resolved_tag_id})
                        logger.info("Fallback: updated knowledge %s tag via update_knowledge", knowledge_id)
                    except Exception:
                        logger.exception("Fallback update_knowledge failed for %s", knowledge_id)
            else:
                logger.warning("Failed to resolve/create tag for date %s", date_tag)
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=(
                    "[WeKnora] 知识已写入。\n"
                    f"标题: {resolved_title}\n"
                    f"知识库: {resolved_kb_id}"
                ),
            ),
        ],
        metadata={"result": result},
    )


def weknora_rag_chat(query: str, session_id: str | None = None, **kwargs: Any) -> ToolResponse:
    """基于知识库执行 RAG 问答。

    Args:
        query: 查询问题。
        session_id: 会话 ID（可选，默认取配置）。
        **kwargs: 其他接口参数。

    Returns:
        ToolResponse，包含 RAG 分析结果。
    """
    global _SESSION_ID
    resolved_session_id = session_id or _SESSION_ID or WEKNORA_CONFIG["session_id"]
    if _SESSION_ID is None and resolved_session_id:
        _SESSION_ID = resolved_session_id
        _save_cache()
    kb_name = kwargs.pop("kb_name", None)
    kb_info = None
    if "knowledge_base_ids" not in kwargs:
        kb_info = _resolve_kb_info(None, kb_name)
        if kb_info and kb_info.get("id"):
            kwargs["knowledge_base_ids"] = [kb_info["id"]]
    if "mentioned_items" not in kwargs and (kb_info or WEKNORA_CONFIG.get("knowledge_base_name")):
        if not kb_info:
            kb_info = _resolve_kb_info(None, kb_name)
        if kb_info and kb_info.get("id"):
            kwargs["mentioned_items"] = [
                {
                    "id": kb_info["id"],
                    "name": kb_info.get("name") or (kb_name or WEKNORA_CONFIG.get("knowledge_base_name")),
                    "type": "kb",
                    "kb_type": kb_info.get("kb_type", "document"),
                }
            ]
    if "agent_enabled" not in kwargs:
        kwargs["agent_enabled"] = True
    if "web_search_enabled" not in kwargs:
        kwargs["web_search_enabled"] = True
    if "agent_id" not in kwargs:
        resolved_agent_id = _resolve_agent_id(None, WEKNORA_CONFIG.get("agent_name"))
        if resolved_agent_id:
            kwargs["agent_id"] = resolved_agent_id
    
    logger.info(f"WeKnora RAG Chat called with query: {query} and kwargs: {kwargs}")
    try:
        result = agent_chat(resolved_session_id, query, **kwargs)
    except requests.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", None)
        if status_code == 404:
            session_payload = {
                "title": "Seneschal 会话",
                "description": "Auto-created by Seneschal",
            }
            created = create_session(session_payload)
            new_session_id = created.get("data", {}).get("id") if isinstance(created, dict) else None
            if new_session_id:
                WEKNORA_CONFIG["session_id"] = new_session_id
                resolved_session_id = new_session_id
                _SESSION_ID = new_session_id
                _save_cache()
                result = agent_chat(resolved_session_id, query, **kwargs)
            else:
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text="[WeKnora] 会话不存在且自动创建失败。",
                        ),
                    ],
                    metadata={"error": str(exc), "create_session": created},
                )
        else:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"[WeKnora] 分析失败: {exc}",
                    ),
                ],
                metadata={"error": str(exc)},
            )
    except Exception as exc:
        logger.exception("WeKnora agent_chat failed")
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"[WeKnora] 分析失败: {exc}",
                ),
            ],
            metadata={"error": str(exc)},
        )

    logger.info(f"WeKnora RAG Chat result: {result}")
    answer = result.get("answer") if isinstance(result, dict) else None
    if not answer and isinstance(result, dict):
        answer = result.get("thinking")
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=(
                    "[WeKnora] 分析完成。\n"
                    f"问题: {query}\n"
                    f"结果: {answer or '已返回结构化结果'}"
                ),
            ),
        ],
        metadata={"result": result},
    )


# Compatibility aliases for older imports
weknora_knowledge_search = knowledge_search
weknora_list_knowledge_bases = list_knowledge_bases

__all__ = [
    "call_mobi_action",
    "call_mobi_collect",
    "call_mobi_collect_verified",
    "run_shell_command",
    "fetch_url_text",
    "fetch_url_readable_text",
    "fetch_url_links",
    "brave_search",
    "write_text_file",
    "arxiv_search",
    "dblp_conference_search",
    "download_file",
    "extract_pdf_text",
    "read_docx_text",
    "create_docx_from_text",
    "edit_docx",
    "create_pdf_from_text",
    "read_xlsx_summary",
    "write_xlsx_from_records",
    "write_xlsx_from_rows",
    "knowledge_chat",
    "agent_chat",
    "knowledge_search",
    "create_knowledge_manual",
    "list_knowledge_bases",
    "weknora_knowledge_search",
    "weknora_list_knowledge_bases",
    "weknora_add_knowledge",
    "weknora_rag_chat",
]
