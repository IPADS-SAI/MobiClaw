"""Device list, show, heartbeat, and remove commands."""
from __future__ import annotations

import asyncio

import click

from .http_client import GatewayClient
from .output import print_table, print_text, render


def register_device_commands(cli_group: click.Group) -> None:
    """Register device list, show, heartbeat, remove subcommands."""

    @cli_group.group("device")
    def device():
        """List, show, heartbeat, or remove devices."""

    @device.command("list")
    @click.pass_context
    def list_cmd(ctx):
        """List devices."""
        from .config import resolve_config

        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        output_fmt = cfg.get("output_fmt", "table")

        result = asyncio.run(client.list_devices())
        devices = result.get("devices") or []

        if output_fmt == "json":
            render(result, "json")
        elif output_fmt == "table" and devices:
            cols = ["device_id", "device_name", "tailscale_ip", "adb_port", "last_heartbeat", "first_seen"]
            rows = [
                [
                    d.get("device_id", ""),
                    d.get("device_name", ""),
                    d.get("tailscale_ip", ""),
                    d.get("adb_port", ""),
                    d.get("last_heartbeat", ""),
                    d.get("first_seen", ""),
                ]
                for d in devices
            ]
            print_table(cols, rows, title="Devices")
        elif output_fmt == "table":
            print_text("No devices found.")
        else:
            render(result, output_fmt)

    @device.command("show")
    @click.argument("device_id", required=True)
    @click.pass_context
    def show(ctx, device_id: str):
        """Show device by device_id."""
        from .config import resolve_config

        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        output_fmt = cfg.get("output_fmt", "table")

        result = asyncio.run(client.get_device(device_id))

        if output_fmt == "json":
            render(result, "json")
        elif output_fmt == "table":
            cols = ["device_id", "device_name", "tailscale_ip", "adb_port", "last_heartbeat", "first_seen"]
            rows = [
                [
                    result.get("device_id", ""),
                    result.get("device_name", ""),
                    result.get("tailscale_ip", ""),
                    result.get("adb_port", ""),
                    result.get("last_heartbeat", ""),
                    result.get("first_seen", ""),
                ]
            ]
            print_table(cols, rows, title=f"Device {device_id}")
        else:
            render(result, output_fmt)

    @device.command("heartbeat")
    @click.option("--device-id", required=True, help="Device ID")
    @click.option("--ip", "tailscale_ip", help="Tailscale IP")
    @click.option("--port", "adb_port", type=int, help="ADB port")
    @click.option("--name", "device_name", help="Device name")
    @click.pass_context
    def heartbeat(ctx, device_id: str, tailscale_ip: str | None, adb_port: int | None, device_name: str | None):
        """Send device heartbeat."""
        from .config import resolve_config

        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        output_fmt = cfg.get("output_fmt", "table")

        result = asyncio.run(
            client.device_heartbeat(
                device_id=device_id,
                tailscale_ip=tailscale_ip,
                adb_port=adb_port,
                device_name=device_name,
            )
        )

        if output_fmt == "json":
            render(result, "json")
        else:
            print_text(f"Heartbeat ok: {result.get('device_id', device_id)} @ {result.get('timestamp', '')}")

    @device.command("remove")
    @click.argument("device_id", required=True)
    @click.option("--yes", "yes_flag", is_flag=True, help="Skip confirmation")
    @click.pass_context
    def remove(ctx, device_id: str, yes_flag: bool):
        """Remove a device by device_id."""
        from .config import resolve_config

        if not yes_flag and not click.confirm(f"Remove device {device_id}?"):
            return

        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        result = asyncio.run(client.remove_device(device_id))

        print_text(f"Removed device {device_id}")
