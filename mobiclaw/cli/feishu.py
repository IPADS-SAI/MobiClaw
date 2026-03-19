"""Feishu event commands."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from .config import resolve_config
from .http_client import GatewayClient
from .output import render


def register_feishu_commands(cli_group: click.Group) -> None:
    """Register feishu send-event subcommand on cli_group."""

    @cli_group.group("feishu", help="Feishu event commands")
    @click.pass_context
    def feishu_group(ctx):
        pass

    @feishu_group.command("send-event", help="Send event to Feishu via gateway")
    @click.argument("payload_file", type=click.Path(exists=True, path_type=Path))
    @click.pass_context
    def send_event(ctx, payload_file: Path):
        """Read JSON from payload_file, POST to gateway, print response."""
        cfg = resolve_config(ctx)
        try:
            payload = json.loads(payload_file.read_text())
        except json.JSONDecodeError as e:
            raise click.ClickException(f"Invalid JSON in {payload_file}: {e}") from e
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        result = asyncio.run(client.send_feishu_event(payload))
        render(result, cfg.get("output_fmt", "table"))
