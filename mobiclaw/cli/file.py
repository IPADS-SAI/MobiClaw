"""File download command."""
from __future__ import annotations

import asyncio
from pathlib import Path

import click

from .http_client import GatewayClient


def register_file_commands(cli_group: click.Group) -> None:
    """Register file download subcommand."""

    @cli_group.group("file")
    def file_cmd():
        """Download files from job results."""

    @file_cmd.command("download")
    @click.argument("job_id", required=True)
    @click.argument("name", required=True)
    @click.option(
        "--output",
        "output_path",
        type=click.Path(path_type=Path),
        default=None,
        help="Output path (default: cwd with file name)",
    )
    @click.pass_context
    def download(ctx, job_id: str, name: str, output_path: Path | None):
        """Download a file from a job result. Streams with progress bar."""
        from .config import resolve_config

        cfg = resolve_config(ctx)
        path = output_path or (Path.cwd() / name)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        asyncio.run(client.download_file(job_id, name, path))
        click.echo(f"Saved to {path}")
