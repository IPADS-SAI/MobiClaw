"""Session list, show, and delete commands."""
from __future__ import annotations

import asyncio

import click

from .http_client import GatewayClient
from .output import print_table, print_text, render


def register_session_commands(cli_group: click.Group) -> None:
    """Register session list, show, delete subcommands."""

    @cli_group.group("session")
    def session():
        """List, show, or delete chat sessions."""

    @session.command("list")
    @click.pass_context
    def list_cmd(ctx):
        """List chat sessions."""
        from .config import resolve_config

        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        output_fmt = cfg.get("output_fmt", "table")

        result = asyncio.run(client.list_sessions())
        sessions = result.get("sessions") or []

        if output_fmt == "json":
            render(result, "json")
        elif output_fmt == "table" and sessions:
            cols = ["context_id", "session_id", "dir_name", "updated_at", "path"]
            rows = [
                [
                    s.get("context_id", ""),
                    s.get("session_id", ""),
                    s.get("dir_name", ""),
                    s.get("updated_at", ""),
                    s.get("path", ""),
                ]
                for s in sessions
            ]
            print_table(cols, rows, title="Sessions")
        elif output_fmt == "table":
            print_text("No sessions found.")
        else:
            render(result, output_fmt)

    @session.command("show")
    @click.argument("context_id", required=True)
    @click.option("--limit", default=20, help="Max messages to show")
    @click.pass_context
    def show(ctx, context_id: str, limit: int):
        """Show session messages by context_id."""
        from .config import resolve_config

        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        output_fmt = cfg.get("output_fmt", "table")

        result = asyncio.run(client.get_session(context_id, limit=limit))
        messages = result.get("messages") or []
        summary = result.get("summary") or {}

        if output_fmt == "json":
            render(result, "json")
        elif output_fmt == "table" and messages:
            cols = ["role", "name", "text", "ts"]
            rows = [
                [
                    m.get("role", ""),
                    m.get("name", ""),
                    (t := str(m.get("text", "")))[:100] + ("..." if len(t) > 100 else ""),
                    m.get("ts", ""),
                ]
                for m in messages
            ]
            title = f"Session {context_id} ({summary.get('message_count', len(messages))} messages)"
            print_table(cols, rows, title=title)
        elif output_fmt == "table":
            print_text(f"Session {context_id}: no messages")
            if summary:
                print_text(f"  dir: {summary.get('path', '')}")
        else:
            render(result, output_fmt)

    @session.command("delete")
    @click.argument("context_id", required=True)
    @click.option("--yes", "yes_flag", is_flag=True, help="Skip confirmation")
    @click.pass_context
    def delete(ctx, context_id: str, yes_flag: bool):
        """Delete a session by context_id."""
        from .config import resolve_config

        if not yes_flag and not click.confirm(f"Delete session {context_id}?"):
            return

        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        result = asyncio.run(client.delete_session(context_id))

        deleted = result.get("deleted", 0)
        print_text(f"Deleted session {context_id} ({deleted} dir(s) removed)")
