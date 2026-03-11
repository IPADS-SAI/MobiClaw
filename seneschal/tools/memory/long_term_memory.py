# -*- coding: utf-8 -*-
"""Seneschal 长期记忆模块。"""

from __future__ import annotations

import os
from pathlib import Path

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from ...config import MEMORY_CONFIG


def _resolve_path() -> Path:
    """解析并返回记忆文件的绝对路径。"""
    return Path(os.path.expanduser(MEMORY_CONFIG["file_path"])).resolve()


def read_memory() -> str:
    """读取 MEMORY.md 全文。文件不存在时返回空字符串。"""
    p = _resolve_path()
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8")


def update_long_term_memory(content: str) -> ToolResponse:
    """将 content 完整覆盖写入 MEMORY.md（自动创建父目录）。"""
    p = _resolve_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return ToolResponse(
        content=[TextBlock(type="text", text=f"长期记忆已更新，共 {len(content)} 字符。")],
        metadata={"file_path": str(p), "length": len(content)},
    )
