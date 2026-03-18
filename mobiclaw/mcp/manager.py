# -*- coding: utf-8 -*-
"""MCP 服务器管理模块 — 动态注册外部 MCP 工具到 Worker Agent。"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from agentscope.mcp import StdIOStatefulClient, HttpStatelessClient, MCPToolFunction
from agentscope.tool import Toolkit
from ..config import MCP_SERVERS_CONFIG, TOOL_CONFIG
from ..tools.decorators import tool_timeout

logger = logging.getLogger(__name__)

_VALID_TRANSPORTS = {"stdio", "sse", "streamable_http"}

# ---------------------------------------------------------------------------
# MCPServerManager
# ---------------------------------------------------------------------------


class MCPServerManager:
    """Singleton managing MCP server connections and their tool functions."""

    def __init__(self) -> None:
        self._config_path: Path = Path(MCP_SERVERS_CONFIG["config_path"]).expanduser()
        # name -> {config, client, tools: list[MCPToolFunction], status, error}
        self._servers: dict[str, dict[str, Any]] = {}

    # -- persistence ---------------------------------------------------------

    def _read_persisted(self) -> list[dict[str, Any]]:
        if not self._config_path.exists():
            return []
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("Failed to read MCP config %s: %s", self._config_path, exc)
            return []

    def _persist(self) -> None:
        configs = [entry["config"] for entry in self._servers.values()]
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(json.dumps(configs, indent=2, ensure_ascii=False), encoding="utf-8")

    # -- public API ----------------------------------------------------------

    async def add_server(self, config: dict[str, Any]) -> dict[str, Any]:
        """Validate *config*, persist it, and kick off a background connect.

        Returns a dict with ``name`` and ``status`` (always ``"connecting"``).
        Raises ``ValueError`` on invalid input.
        """
        name = (config.get("name") or "").strip()
        if not name:
            raise ValueError("MCP server config must include a non-empty 'name'")
        transport = (config.get("transport") or "").strip()
        if transport not in _VALID_TRANSPORTS:
            raise ValueError(f"Unsupported transport '{transport}', must be one of {_VALID_TRANSPORTS}")

        if transport == "stdio":
            if not config.get("command"):
                raise ValueError("stdio transport requires a 'command' field")
        else:
            if not config.get("url"):
                raise ValueError(f"{transport} transport requires a 'url' field")

        if name in self._servers:
            raise ValueError(f"MCP server '{name}' is already registered")

        self._servers[name] = {
            "config": config,
            "client": None,
            "tools": [],
            "status": "connecting",
            "error": None,
        }
        self._persist()

        # Fire-and-forget background connect
        try:
            asyncio.create_task(self._connect_server(name, config))
        except RuntimeError:
            # No running loop — will be connected lazily or via load_saved_servers
            self._servers[name]["status"] = "pending"

        return {"name": name, "status": self._servers[name]["status"]}

    async def _connect_server(self, name: str, config: dict[str, Any]) -> None:
        """Background task: create client, connect, resolve MCPToolFunction objects."""
        entry = self._servers.get(name)
        if entry is None:
            return

        transport = config["transport"]
        try:
            client: Any
            tools: list[Any] = []

            if transport == "stdio":
                client = StdIOStatefulClient(
                    name=name,
                    command=config["command"],
                    args=config.get("args") or [],
                    env=config.get("env") or None,
                )
                await client.connect()
                mcp_tools = await client.list_tools()
                for t in mcp_tools:
                    func = await client.get_callable_function(t.name)
                    tools.append(func)

            elif transport in ("sse", "streamable_http"):
                client = HttpStatelessClient(
                    name=name,
                    transport=transport,
                    url=config["url"],
                    headers=config.get("headers") or None,
                )
                mcp_tools = await client.list_tools()
                for t in mcp_tools:
                    func = await client.get_callable_function(t.name)
                    tools.append(func)
            else:
                raise ValueError(f"Unsupported transport: {transport}")

            entry["client"] = client
            entry["tools"] = tools
            entry["status"] = "connected"
            entry["error"] = None
            logger.info(
                "MCP server '%s' connected — %d tool(s): %s",
                name,
                len(tools),
                [t.name for t in tools],
            )

        except Exception as exc:
            entry["status"] = "error"
            entry["error"] = str(exc)
            logger.warning("MCP server '%s' failed to connect: %s", name, exc)

    async def remove_server(self, name: str) -> bool:
        """Disconnect and remove an MCP server. Returns True if found."""
        entry = self._servers.pop(name, None)
        if entry is None:
            return False

        client = entry.get("client")
        if client is not None and hasattr(client, "close"):
            try:
                if hasattr(client, "is_connected") and client.is_connected:
                    await client.close()
            except Exception as exc:
                logger.warning("Error closing MCP client '%s': %s", name, exc)

        self._persist()
        return True

    async def shutdown(self) -> None:
        """Close all connected MCP clients. Called during application shutdown."""
        for name, entry in list(self._servers.items()):
            client = entry.get("client")
            if client is not None and hasattr(client, "close"):
                try:
                    if hasattr(client, "is_connected") and client.is_connected:
                        await client.close()
                except Exception as exc:
                    logger.warning("Error closing MCP client '%s' on shutdown: %s", name, exc)
                entry["client"] = None
                entry["status"] = "disconnected"

    def register_tools_with_timeout(self, toolkit: Toolkit, timeout_s: float) -> None:
        """Register all MCP tools into *toolkit* with timeout wrapping.

        After wrapping, the function is no longer an ``MCPToolFunction``
        instance, so we explicitly pass ``func_name`` and ``json_schema``
        to ``register_tool_function`` — otherwise agentscope would fall
        through to its "normal function" branch and lose the MCP-provided
        parameter schema.
        """
        for name, entry in self._servers.items():
            if entry["status"] != "connected":
                continue
            for tool_func in entry["tools"]:
                try:
                    wrapped = tool_timeout(timeout_s)(tool_func)
                    toolkit.register_tool_function(
                        wrapped,
                        func_name=tool_func.name,
                        func_description=tool_func.description or tool_func.name,
                        json_schema=tool_func.json_schema,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to register MCP tool '%s' from server '%s': %s",
                        getattr(tool_func, "name", "?"),
                        name,
                        exc,
                    )

    def list_servers(self) -> list[dict[str, Any]]:
        """Return current server configs, statuses and tool names."""
        result: list[dict[str, Any]] = []
        for name, entry in self._servers.items():
            result.append({
                "name": name,
                "transport": entry["config"].get("transport"),
                "status": entry["status"],
                "error": entry["error"],
                "tools": [t.name for t in entry["tools"]],
                "config": {k: v for k, v in entry["config"].items() if k not in ("env",)},
            })
        return result

    async def load_saved_servers(self) -> None:
        """On startup, load saved configs and connect in parallel."""
        configs = self._read_persisted()
        if not configs:
            return

        tasks = []
        for cfg in configs:
            name = (cfg.get("name") or "").strip()
            if not name or name in self._servers:
                continue
            transport = (cfg.get("transport") or "").strip()
            if transport not in _VALID_TRANSPORTS:
                logger.warning("Skipping saved MCP server '%s': invalid transport '%s'", name, transport)
                continue
            self._servers[name] = {
                "config": cfg,
                "client": None,
                "tools": [],
                "status": "connecting",
                "error": None,
            }
            tasks.append(self._connect_server(name, cfg))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for cfg, result in zip(configs, results):
                if isinstance(result, Exception):
                    logger.warning(
                        "MCP server '%s' failed on startup: %s",
                        cfg.get("name", "?"),
                        result,
                    )

    def get_tool_names(self) -> list[str]:
        """Return all MCP tool names (only from connected servers)."""
        names: list[str] = []
        for entry in self._servers.values():
            if entry["status"] == "connected":
                names.extend(t.name for t in entry["tools"])
        return names

    def get_tool_functions(self) -> list[tuple[Any, str]]:
        """Return (MCPToolFunction, description) pairs for all connected tools."""
        result: list[tuple[Any, str]] = []
        for entry in self._servers.values():
            if entry["status"] == "connected":
                for t in entry["tools"]:
                    result.append((t, t.description or t.name))
        return result


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager: MCPServerManager | None = None


def get_mcp_manager() -> MCPServerManager | None:
    """Return the global MCPServerManager, or None if MCP is unavailable."""
    global _manager
    if _manager is not None:
        return _manager
    _manager = MCPServerManager()
    return _manager
