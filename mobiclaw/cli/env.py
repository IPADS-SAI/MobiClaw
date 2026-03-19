"""Gateway environment variable management commands."""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile

import click

from .config import resolve_config
from .http_client import GatewayClient
from .output import print_table, print_text, render


def _managed_keys_from_schema(schema: list) -> set[str]:
    """Extract managed env keys from schema."""
    keys: set[str] = set()
    for group in schema or []:
        for item in group.get("items", []):
            key = item.get("key")
            if key:
                keys.add(key)
    return keys


def register_env_commands(cli_group: click.Group) -> None:
    """Register env show/set/edit subcommands on cli_group."""

    @cli_group.group("env", help="Gateway environment variable management")
    @click.pass_context
    def env_group(ctx):
        pass

    @env_group.command("show", help="Display environment variables")
    @click.option("--schema", is_flag=True, help="Show schema view (values + unmanaged)")
    @click.pass_context
    def env_show(ctx, schema):
        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        output_fmt = cfg.get("output_fmt", "table")
        if schema:
            result = asyncio.run(client.get_env_schema())
            values = result.get("values", {})
            unmanaged = result.get("unmanaged", {})
            if output_fmt == "json":
                render(result, "json")
            else:
                # Render values + unmanaged as key-value tables
                if values:
                    print_text("\n[bold]Managed (schema):[/bold]")
                    rows = [[k, v] for k, v in sorted(values.items())]
                    print_table(["KEY", "VALUE"], rows)
                if unmanaged:
                    print_text("\n[bold]Unmanaged:[/bold]")
                    rows = [[k, v] for k, v in sorted(unmanaged.items())]
                    print_table(["KEY", "VALUE"], rows)
                if not values and not unmanaged:
                    print_text("(no variables)")
        else:
            result = asyncio.run(client.get_env())
            content = result.get("content", "")
            variables = result.get("variables", {})
            if output_fmt == "json":
                render(result, "json")
            else:
                if content:
                    print_text(f"\n[bold]Content ({result.get('path', '.env')}):[/bold]\n")
                    print_text(content)
                if variables:
                    print_text("\n[bold]Variables:[/bold]")
                    rows = [[k, v] for k, v in sorted(variables.items())]
                    print_table(["KEY", "VALUE"], rows)
                if not content and not variables:
                    print_text("(empty)")

    @env_group.command("set", help="Set single environment variable")
    @click.argument("key")
    @click.argument("value")
    @click.pass_context
    def env_set(ctx, key, value):
        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        result = asyncio.run(client.get_env_schema())
        values = dict(result.get("values", {}))
        unmanaged = dict(result.get("unmanaged", {}))
        schema = result.get("schema", [])
        managed_keys = _managed_keys_from_schema(schema)
        if key in managed_keys:
            values[key] = value
            asyncio.run(
                client.set_env_structured(values, preserve_unmanaged=True)
            )
        else:
            unmanaged[key] = value
            asyncio.run(
                client.set_env_structured(
                    values, unmanaged=unmanaged, preserve_unmanaged=False
                )
            )
        render({"ok": True, "key": key, "value": value}, cfg.get("output_fmt", "table"))
        print_text("\nRestart gateway server for changes to take effect.")

    @env_group.command("edit", help="Edit .env content in $EDITOR")
    @click.pass_context
    def env_edit(ctx):
        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        result = asyncio.run(client.get_env())
        content = result.get("content", "")
        editor = os.environ.get("EDITOR") or "nano"
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".env",
            delete=False,
        ) as f:
            f.write(content)
            tmp_path = f.name
        try:
            subprocess.run([editor, tmp_path], check=True)
            new_content = open(tmp_path, encoding="utf-8").read()
            asyncio.run(client.set_env_content(new_content))
            render(
                {"ok": True, "path": result.get("path", ".env")},
                cfg.get("output_fmt", "table"),
            )
            print_text("\nRestart gateway server for changes to take effect.")
        finally:
            os.unlink(tmp_path)
