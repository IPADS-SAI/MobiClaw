"""MobiClaw CLI root."""
import asyncio

import click

from .config import load_cli_config, register_config_commands
from .http_client import GatewayClient
from .output import render


def resolve_config(ctx: click.Context) -> dict:
    """Merge ctx.obj overrides with load_cli_config(). Priority: ctx.obj > config file > defaults."""
    ctx.ensure_object(dict)
    file_cfg = load_cli_config()
    return {
        "server_url": ctx.obj.get("server_url") or file_cfg.get("server_url") or "http://localhost:8090",
        "api_key": ctx.obj.get("api_key") or file_cfg.get("api_key") or "",
        "output_fmt": ctx.obj.get("output_fmt") or file_cfg.get("default_output") or "table",
        "verbose": ctx.obj.get("verbose", False),
    }


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


@cli.command()
@click.pass_context
def health(ctx):
    """Health check."""
    cfg = resolve_config(ctx)
    client = GatewayClient(cfg["server_url"], cfg["api_key"])
    result = asyncio.run(client.health())
    render(result, cfg.get("output_fmt", "table"))
