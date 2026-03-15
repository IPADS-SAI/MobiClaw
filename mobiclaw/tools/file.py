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
