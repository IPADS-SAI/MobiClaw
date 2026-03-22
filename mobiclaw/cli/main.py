"""MobiClaw CLI root."""
import asyncio

import click

from .chat import register_chat_command
from .config import load_cli_config, register_config_commands, resolve_config
from .device import register_device_commands
from .env import register_env_commands
from .feishu import register_feishu_commands
from .file import register_file_commands
from .http_client import GatewayClient
from .mcp import register_mcp_commands
from .output import render
from .schedule import register_schedule_commands
from .session import register_session_commands
from .task import register_task_commands


@click.group()
@click.option("--server-url", envvar="MOBICLAW_SERVER_URL", help="Gateway server URL")
@click.option("--api-key", envvar="MOBICLAW_API_KEY", help="API key for auth")
@click.option("--output", "output_fmt", type=click.Choice(["json", "table", "text"]), default="table")
@click.option("--verbose", is_flag=True)
@click.pass_context
def cli(ctx, server_url, api_key, output_fmt, verbose):
    ctx.ensure_object(dict)
    ctx.obj["server_url"] = server_url
    ctx.obj["api_key"] = api_key
    ctx.obj["output_fmt"] = output_fmt
    ctx.obj["verbose"] = verbose


register_config_commands(cli)
register_chat_command(cli)
register_task_commands(cli)
register_mcp_commands(cli)
register_schedule_commands(cli)
register_session_commands(cli)
register_env_commands(cli)
register_device_commands(cli)
register_file_commands(cli)
register_feishu_commands(cli)


@cli.command()
@click.pass_context
def health(ctx):
    """Health check."""
    cfg = resolve_config(ctx)
    client = GatewayClient(cfg["server_url"], cfg["api_key"])
    result = asyncio.run(client.health())
    render(result, cfg.get("output_fmt", "table"))
