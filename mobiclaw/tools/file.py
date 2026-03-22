# -*- coding: utf-8 -*-
"""Local file write tool."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

logger = logging.getLogger(__name__)


def write_text_file(
    path: str,
    content: str,
    mode: str = "w",
    encoding: str = "utf-8",
) -> ToolResponse:
    """Write text content to a local file.

    If MOBICLAW_FILE_WRITE_ROOT is set, all writes are constrained to that root.

    Args:
        path: Target file path.
        content: Text content to write.
        mode: File mode, supports ``w`` (overwrite) and ``a`` (append).
        encoding: Text encoding used when writing.
    """
    resolved_path = (path or "").strip()
    if not resolved_path:
        return ToolResponse(
            content=[TextBlock(type="text", text="[File] Empty path.")],
            metadata={"error": "empty_path"},
        )

    normalized_mode = (mode or "w").strip().lower()
    if normalized_mode not in {"w", "a"}:
        return ToolResponse(
            content=[TextBlock(type="text", text="[File] Unsupported mode. Use 'w' or 'a'.")],
            metadata={"error": "invalid_mode", "mode": mode},
        )

    root = (os.environ.get("MOBICLAW_FILE_WRITE_ROOT") or "").strip()
    target = Path(resolved_path).expanduser()
    if root:
        root_path = Path(root).expanduser().resolve()
        target = target if target.is_absolute() else root_path / target
        try:
            target = target.resolve()
        except FileNotFoundError:
            target = target.absolute()
        if target != root_path and root_path not in target.parents:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="[File] Path is outside MOBICLAW_FILE_WRITE_ROOT.",
                    )
                ],
                metadata={"error": "path_outside_root", "root": str(root_path)},
            )
    else:
        target = target.resolve()

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open(normalized_mode, encoding=encoding) as handle:
            handle.write(content or "")
    except Exception as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[File] Write failed: {exc}")],
            metadata={"error": str(exc)},
        )

    logger.info("file.write path=%s mode=%s size=%d", target, normalized_mode, len(content or ""))
    return ToolResponse(
        content=[TextBlock(type="text", text=f"[File] Wrote: {target}")],
        metadata={"path": str(target), "mode": normalized_mode},
    )


def read_markdown_file(
    path: str,
    encoding: str = "utf-8",
    max_chars: int = 20000,
) -> ToolResponse:
    """Read a local Markdown file.

    If MOBICLAW_FILE_READ_ROOT is set, reads are constrained to that root.

    Args:
        path: Target markdown file path.
        encoding: Text encoding used when reading.
        max_chars: Maximum number of characters to return.
    """
    resolved_path = (path or "").strip()
    if not resolved_path:
        return ToolResponse(
            content=[TextBlock(type="text", text="[File] Empty path.")],
            metadata={"error": "empty_path"},
        )

    if max_chars <= 0:
        return ToolResponse(
            content=[TextBlock(type="text", text="[File] max_chars must be greater than 0.")],
            metadata={"error": "invalid_max_chars", "max_chars": max_chars},
        )

    target = Path(resolved_path).expanduser()
    root = (os.environ.get("MOBICLAW_FILE_READ_ROOT") or "").strip()
    if root:
        root_path = Path(root).expanduser().resolve()
        target = target if target.is_absolute() else root_path / target
        try:
            target = target.resolve()
        except FileNotFoundError:
            target = target.absolute()
        if target != root_path and root_path not in target.parents:
            return ToolResponse(
                content=[TextBlock(type="text", text="[File] Path is outside MOBICLAW_FILE_READ_ROOT.")],
                metadata={"error": "path_outside_root", "root": str(root_path)},
            )
    else:
        target = target.resolve()

    if not target.exists() or not target.is_file():
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[File] File not found: {target}")],
            metadata={"error": "file_not_found", "path": str(target)},
        )

    if target.suffix.lower() != ".md":
        return ToolResponse(
            content=[TextBlock(type="text", text="[File] Only .md files are supported.")],
            metadata={"error": "invalid_extension", "path": str(target), "suffix": target.suffix.lower()},
        )

    try:
        content = target.read_text(encoding=encoding)
    except Exception as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[File] Read failed: {exc}")],
            metadata={"error": "read_failed", "path": str(target)},
        )

    is_truncated = len(content) > max_chars
    output = content[:max_chars] if is_truncated else content
    logger.info("file.read_markdown path=%s chars=%d truncated=%s", target, len(output), is_truncated)
    return ToolResponse(
        content=[TextBlock(type="text", text=output)],
        metadata={
            "path": str(target),
            "encoding": encoding,
            "truncated": is_truncated,
            "returned_chars": len(output),
            "total_chars": len(content),
        },
    )
