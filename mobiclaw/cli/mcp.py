"""MCP server management commands."""
from __future__ import annotations

import asyncio

import click

from .config import resolve_config
from .http_client import GatewayClient
from .output import print_table, render


def register_mcp_commands(cli_group: click.Group) -> None:
    """Register mcp list/add/remove subcommands on cli_group."""

    @cli_group.group("mcp", help="MCP server management")
    @click.pass_context
    def mcp_group(ctx):
        pass

    @mcp_group.command("list", help="List MCP servers")
    @click.pass_context
    def mcp_list(ctx):
        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        result = asyncio.run(client.list_mcp_servers())
        servers = result.get("servers", [])
        output_fmt = cfg.get("output_fmt", "table")
        if output_fmt == "json":
            render(result, "json")
        elif servers:
            cols = ["name", "transport", "status", "tools"]
            rows = [
                [
                    s.get("name"),
                    s.get("transport"),
                    s.get("status"),
                    ", ".join(s.get("tools") or []) if s.get("tools") else "-",
                ]
                for s in servers
            ]
            print_table(cols, rows)
        else:
            render({"servers": [], "enabled": result.get("enabled", False)}, output_fmt)

    @mcp_group.command("add", help="Add MCP server (stdio or sse transport)")
    @click.argument("name")
    @click.argument("command", required=False)
    @click.option("--args", "args_list", multiple=True, help="Arguments for stdio command")
    @click.option("--env", "env_pairs", multiple=True, help="Env vars KEY=VALUE for stdio")
    @click.option("--url", help="URL for SSE transport")
    @click.option("--transport", type=click.Choice(["sse"]), default="sse", help="Transport when using --url (default: sse)")
    @click.pass_context
    def mcp_add(ctx, name, command, args_list, env_pairs, url, transport):
        cfg = resolve_config(ctx)
        if url:
            body = {"name": name, "transport": transport, "url": url}
        elif command:
            env_dict = {}
            for pair in env_pairs:
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    env_dict[k.strip()] = v.strip()
                else:
                    raise click.BadParameter(f"Invalid env: {pair!r}, use KEY=VALUE")
            body = {
                "name": name,
                "transport": "stdio",
                "command": command,
                "args": list(args_list),
                "env": env_dict if env_dict else None,
            }
        else:
            raise click.UsageError(
                "Either provide <command> for stdio transport, or --url for SSE transport"
            )
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        result = asyncio.run(client.add_mcp_server(body))
        render(result, cfg.get("output_fmt", "table"))

    @mcp_group.command("remove", help="Remove MCP server")
    @click.argument("name")
    @click.option("--yes", "-y", "skip_confirm", is_flag=True, help="Skip confirmation")
    @click.pass_context
    def mcp_remove(ctx, name, skip_confirm):
        if not skip_confirm:
            click.confirm(f"Remove MCP server '{name}'?", abort=True)
        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        result = asyncio.run(client.remove_mcp_server(name))
        render(result, cfg.get("output_fmt", "table"))
