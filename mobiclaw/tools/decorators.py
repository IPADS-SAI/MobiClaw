# -*- coding: utf-8 -*-
"""Tool timeout decorator for agent tools."""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import os

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

logger = logging.getLogger(__name__)


DEFAULT_TOOL_TIMEOUT_S = 120


def tool_timeout(seconds: float | None = None):
    """Decorator that wraps a tool function with an async timeout.

    If the tool does not complete within *seconds*, returns a ``ToolResponse``
    describing the timeout instead of raising an exception.

    Works with both sync and async tool functions.

    Args:
        seconds: Timeout in seconds.  Defaults to ``DEFAULT_TOOL_TIMEOUT_S``.
    """
    timeout_s = seconds if seconds is not None else DEFAULT_TOOL_TIMEOUT_S

    def decorator(func):
        is_async = inspect.iscoroutinefunction(func) or inspect.iscoroutinefunction(
            getattr(func, "__call__", None),
        )
        # MCPToolFunction instances have .name but not __name__;
        # functools.wraps also requires __name__, so we patch it on first.
        _tool_name = getattr(func, "__name__", None) or getattr(func, "name", None) or "unknown_tool"
        if not hasattr(func, "__name__"):
            func.__name__ = _tool_name
        if not hasattr(func, "__doc__"):
            func.__doc__ = getattr(func, "description", None) or ""

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            tool_name = _tool_name
            try:
                if is_async:
                    result = await asyncio.wait_for(
                        func(*args, **kwargs),
                        timeout=timeout_s,
                    )
                else:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(func, *args, **kwargs),
                        timeout=timeout_s,
                    )
                return result
            except asyncio.TimeoutError:
                msg = (
                    f"[Tool Timeout] 工具 \"{tool_name}\" 在 {timeout_s:.0f} 秒内未完成，已超时终止。"
                    f" 调用参数: args={args}, kwargs={kwargs}"
                )
                logger.warning(msg)
                return ToolResponse(
                    content=[TextBlock(type="text", text=msg)],
                    metadata={"timeout": True, "tool": tool_name, "timeout_seconds": timeout_s},
                )
            except Exception as exc:
                msg = (
                    f"[Tool Error] 工具 \"{tool_name}\" 执行出错: {type(exc).__name__}: {exc}"
                )
                logger.exception(msg)
                return ToolResponse(
                    content=[TextBlock(type="text", text=msg)],
                    metadata={"error": True, "tool": tool_name, "error_type": type(exc).__name__, "error_msg": str(exc)},
                )

        return wrapper

    return decorator
