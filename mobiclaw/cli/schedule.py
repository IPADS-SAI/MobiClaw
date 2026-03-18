"""Schedule list and cancel commands."""
from __future__ import annotations

import asyncio

import click

from .http_client import GatewayClient
from .output import print_text, render


def register_schedule_commands(cli_group: click.Group) -> None:
    """Register schedule list and cancel subcommands."""

    @cli_group.group("schedule")
    def schedule():
        """List or cancel scheduled tasks."""

    @schedule.command("list")
    @click.pass_context
    def list_cmd(ctx):
        """List scheduled tasks."""
        from .config import resolve_config

        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        output_fmt = cfg.get("output_fmt", "table")
        result = asyncio.run(client.list_schedules())
        schedules = result.get("schedules", [])
        render(schedules, output_fmt)

    @schedule.command("cancel")
    @click.argument("schedule_id", required=True)
    @click.option("--yes", "yes_flag", is_flag=True, help="Skip confirmation")
    @click.pass_context
    def cancel(ctx, schedule_id: str, yes_flag: bool):
        """Cancel a scheduled task."""
        from .config import resolve_config

        if not yes_flag and not click.confirm("Cancel?", default=False):
            raise click.Abort()

        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        result = asyncio.run(client.cancel_schedule(schedule_id))
        print_text(f"Cancelled: {result.get('schedule_id', schedule_id)}")
