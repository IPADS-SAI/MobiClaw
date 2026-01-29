# -*- coding: utf-8 -*-
"""Seneschal 工具包。"""

from __future__ import annotations

import json
from typing import Any

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse
import requests

from .mobi import call_mobi_action, call_mobi_collect
from .weknora import (
    knowledge_chat,
    agent_chat,
    knowledge_search,
    create_knowledge_manual,
    list_knowledge_bases,
    create_session,
)
from ..config import WEKNORA_CONFIG


def weknora_add_knowledge(
    content: str | dict[str, Any] | list[Any],
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
    kb_id: str | None = None,
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
    resolved_kb_id = kb_id or WEKNORA_CONFIG["knowledge_base_id"]
    result = create_knowledge_manual(
        resolved_kb_id,
        title=resolved_title,
        content=content,
        metadata=metadata,
    )
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
    resolved_session_id = session_id or WEKNORA_CONFIG["session_id"]
    if "knowledge_base_ids" not in kwargs and WEKNORA_CONFIG.get("knowledge_base_id"):
        kwargs["knowledge_base_ids"] = [WEKNORA_CONFIG["knowledge_base_id"]]
    try:
        result = knowledge_chat(resolved_session_id, query, **kwargs)
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
                result = knowledge_chat(resolved_session_id, query, **kwargs)
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
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"[WeKnora] 分析失败: {exc}",
                ),
            ],
            metadata={"error": str(exc)},
        )

    answer = result.get("answer") if isinstance(result, dict) else None
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

__all__ = [
    "call_mobi_action",
    "call_mobi_collect",
    "knowledge_chat",
    "agent_chat",
    "knowledge_search",
    "create_knowledge_manual",
    "list_knowledge_bases",
    "weknora_add_knowledge",
    "weknora_rag_chat",
]
