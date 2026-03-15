# -*- coding: utf-8 -*-
"""MobiClaw RAG module — local vector knowledge base backed by agentscope."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentscope.message import TextBlock
from agentscope.rag import (
    Document,
    SimpleKnowledge,
    QdrantStore,
    TextReader,
    PDFReader,
    WordReader,
    ExcelReader,
)
from agentscope.embedding import OpenAITextEmbedding
from agentscope.tool import ToolResponse

from ...config import MODEL_CONFIG, RAG_CONFIG

logger = logging.getLogger(__name__)

# Two separate singletons: task_history (system-managed) and knowledge (agent-managed)
_task_history_kb: SimpleKnowledge | None = None
_knowledge_kb: SimpleKnowledge | None = None
_init_lock = asyncio.Lock()


def _create_embedding_model() -> OpenAITextEmbedding:
    """Create a shared embedding model instance."""
    return OpenAITextEmbedding(
        api_key=MODEL_CONFIG["api_key"],
        model_name=RAG_CONFIG["embedding_model"],
        dimensions=RAG_CONFIG["embedding_dimensions"],
        base_url=MODEL_CONFIG["api_base"],
    )


async def _init_task_history() -> SimpleKnowledge:
    """Lazy-init singleton for task history (system writes, agent reads)."""
    global _task_history_kb

    async with _init_lock:
        if _task_history_kb is not None:
            return _task_history_kb

        store_path = Path(RAG_CONFIG["store_path"]).expanduser().resolve()
        store_path.mkdir(parents=True, exist_ok=True)

        store = QdrantStore(
            location=None,  # type: ignore[arg-type] — use path for local storage
            collection_name=RAG_CONFIG["collection_name"] + "_history",
            dimensions=RAG_CONFIG["embedding_dimensions"],
            client_kwargs={"path": str(store_path)},
        )
        _task_history_kb = SimpleKnowledge(
            embedding_store=store,
            embedding_model=_create_embedding_model(),
        )
        logger.info("RAG task_history initialized: %s", store_path)
    return _task_history_kb


async def _init_knowledge() -> SimpleKnowledge:
    """Lazy-init singleton for agent knowledge (agent reads and writes)."""
    global _knowledge_kb

    async with _init_lock:
        if _knowledge_kb is not None:
            return _knowledge_kb

        store_path = Path(RAG_CONFIG["store_path"]).expanduser().resolve()
        store_path.mkdir(parents=True, exist_ok=True)

        store = QdrantStore(
            location=None,  # type: ignore[arg-type] — use path for local storage
            collection_name=RAG_CONFIG["collection_name"] + "_knowledge",
            dimensions=RAG_CONFIG["embedding_dimensions"],
            client_kwargs={"path": str(store_path)},
        )
        _knowledge_kb = SimpleKnowledge(
            embedding_store=store,
            embedding_model=_create_embedding_model(),
        )
        logger.info("RAG knowledge initialized: %s", store_path)
    return _knowledge_kb


# Reader factories keyed by file extension
_FILE_READERS: dict[str, type] = {
    ".txt": TextReader,
    ".md": TextReader,
    ".csv": TextReader,
    ".json": TextReader,
    ".pdf": PDFReader,
    ".docx": WordReader,
    ".xlsx": ExcelReader,
}


# ---------------------------------------------------------------------------
# Task history: system writes after job completion, agent reads only
# ---------------------------------------------------------------------------

async def store_task_result(
    job_id: str,
    task: str,
    reply: str,
    files: list[dict[str, Any]] | None = None,
    timestamp: str | None = None,
) -> None:
    """Store a completed task result into task history.

    Called by gateway_server._run_job() after successful completion.
    """
    kb = await _init_task_history()
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    chunk_size = RAG_CONFIG["chunk_size"]
    reader = TextReader(chunk_size=chunk_size, split_by="char")

    # Build summary text
    file_meta_lines: list[str] = []
    if files:
        for f in files:
            name = f.get("name", "")
            path = f.get("path", "")
            file_meta_lines.append(f"  - {name} ({path})")
    file_section = "\n".join(file_meta_lines) if file_meta_lines else "  (none)"

    summary = (
        f"[Task Record]\n"
        f"job_id: {job_id}\n"
        f"timestamp: {ts}\n"
        f"task: {task}\n"
        f"reply:\n{reply}\n"
        f"files:\n{file_section}\n"
    )

    documents = await reader(summary)
    logger.info("RAG: indexing task summary for job_id=%s, %d chunks", job_id, len(documents))

    # Optionally index file contents
    if RAG_CONFIG["index_file_content"] and files:
        for f in files:
            path_str = (f.get("path") or "").strip()
            if not path_str:
                continue
            file_path = Path(path_str).expanduser()
            if not file_path.exists():
                continue
            ext = file_path.suffix.lower()
            reader_cls = _FILE_READERS.get(ext)
            if reader_cls is None:
                continue
            try:
                file_reader = reader_cls(chunk_size=chunk_size, split_by="char")
                if reader_cls is TextReader:
                    text = file_path.read_text(encoding="utf-8", errors="replace")
                    file_docs = await file_reader(text)
                else:
                    file_docs = await file_reader(str(file_path))
                documents.extend(file_docs)
                logger.info("RAG: indexed file %s, %d chunks", file_path.name, len(file_docs))
            except Exception:
                logger.warning("RAG: failed to index file %s", path_str, exc_info=True)

    await kb.add_documents(documents)
    logger.info("RAG: stored %d total chunks for job_id=%s", len(documents), job_id)


async def search_task_history(query: str, limit: int = 5) -> ToolResponse:
    """检索历史任务执行记录，用于回答关于之前做过的任务的问题。

    Args:
        query: 检索关键词或自然语言问题。
        limit: 返回结果数量上限，默认5条。

    Returns:
        ToolResponse 包含检索到的历史任务片段。
    """
    try:
        kb = await _init_task_history()
    except Exception as exc:
        logger.warning("RAG task_history not available: %s", exc)
        return ToolResponse(
            content=[TextBlock(type="text", text="[RAG] 任务历史库未就绪，无法检索。")],
            metadata={"error": str(exc)},
        )

    try:
        results: list[Document] = await kb.retrieve(query, limit=limit)
    except Exception as exc:
        logger.warning("RAG task_history retrieval failed: %s", exc)
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[RAG] 检索失败: {exc}")],
            metadata={"error": str(exc)},
        )

    if not results:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[RAG] 未找到与 '{query}' 相关的历史任务记录。")],
            metadata={"query": query, "count": 0},
        )

    parts: list[str] = [f"[RAG] 检索到 {len(results)} 条相关记录：\n"]
    for i, doc in enumerate(results, 1):
        content = doc.metadata.content
        text = content.text if hasattr(content, "text") else str(content)
        score = f" (score={doc.score:.3f})" if doc.score is not None else ""
        parts.append(f"--- 结果 {i}{score} ---\n{text}\n")

    return ToolResponse(
        content=[TextBlock(type="text", text="\n".join(parts))],
        metadata={"query": query, "count": len(results)},
    )


# ---------------------------------------------------------------------------
# Knowledge: agent reads and writes
# ---------------------------------------------------------------------------

async def store_steward_knowledge(content: str, title: str = "") -> ToolResponse:
    """将文本内容存入本地知识库，供后续检索使用。

    Args:
        content: 需要存储的文本内容（OCR 文字、对话记录、账单信息等）。
        title: 知识标题（可选）。

    Returns:
        ToolResponse，包含存储结果。
    """
    if not content or not content.strip():
        return ToolResponse(
            content=[TextBlock(type="text", text="[RAG] 内容为空，未存储。")],
            metadata={"stored": False},
        )

    try:
        kb = await _init_knowledge()
    except Exception as exc:
        logger.warning("RAG knowledge not available: %s", exc)
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[RAG] 知识库未就绪，存储失败: {exc}")],
            metadata={"error": str(exc)},
        )

    ts = datetime.now(timezone.utc).isoformat()
    header = f"[Knowledge Record]\ntitle: {title}\ntimestamp: {ts}\n\n" if title else ""
    text = header + content

    reader = TextReader(chunk_size=RAG_CONFIG["chunk_size"], split_by="char")
    documents = await reader(text)
    await kb.add_documents(documents)

    logger.info("RAG: stored knowledge '%s', %d chunks", title or "(untitled)", len(documents))
    return ToolResponse(
        content=[TextBlock(type="text", text=(
            f"[RAG] 知识已存入本地知识库。\n"
            f"标题: {title or '(无标题)'}\n"
            f"分块数: {len(documents)}"
        ))],
        metadata={"stored": True, "chunk_count": len(documents)},
    )


async def search_steward_knowledge(query: str, limit: int = 5) -> ToolResponse:
    """检索本地知识库中已存储的信息。

    Args:
        query: 检索关键词或自然语言问题。
        limit: 返回结果数量上限，默认5条。

    Returns:
        ToolResponse 包含检索到的知识片段。
    """
    try:
        kb = await _init_knowledge()
    except Exception as exc:
        logger.warning("RAG knowledge not available: %s", exc)
        return ToolResponse(
            content=[TextBlock(type="text", text="[RAG] 知识库未就绪，无法检索。")],
            metadata={"error": str(exc)},
        )

    try:
        results: list[Document] = await kb.retrieve(query, limit=limit)
    except Exception as exc:
        logger.warning("RAG knowledge retrieval failed: %s", exc)
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[RAG] 检索失败: {exc}")],
            metadata={"error": str(exc)},
        )

    if not results:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[RAG] 未找到与 '{query}' 相关的知识记录。")],
            metadata={"query": query, "count": 0},
        )

    parts: list[str] = [f"[RAG] 检索到 {len(results)} 条相关知识：\n"]
    for i, doc in enumerate(results, 1):
        content = doc.metadata.content
        text = content.text if hasattr(content, "text") else str(content)
        score = f" (score={doc.score:.3f})" if doc.score is not None else ""
        parts.append(f"--- 结果 {i}{score} ---\n{text}\n")

    return ToolResponse(
        content=[TextBlock(type="text", text="\n".join(parts))],
        metadata={"query": query, "count": len(results)},
    )
