"""Tests for mobiclaw.cli.device."""
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from mobiclaw.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_device_list(runner):
    """List devices renders table."""
    with patch("mobiclaw.cli.device.GatewayClient") as mock_cls:
        mock_cls.return_value.list_devices = AsyncMock(
            return_value={
                "devices": [
                    {
                        "device_id": "dev1",
                        "device_name": "Pixel 6",
                        "tailscale_ip": "100.1.2.3",
                        "adb_port": 5555,
                        "last_heartbeat": "2026-03-18T10:00:00Z",
                        "first_seen": "2026-03-18T09:00:00Z",
                    },
                ],
                "count": 1,
            }
        )
        r = runner.invoke(cli, ["device", "list"])
    assert r.exit_code == 0
    assert "dev1" in r.output
    assert "Pixel 6" in r.output
    assert "100.1.2.3" in r.output


def test_device_list_empty(runner):
    """List with no devices prints message."""
    with patch("mobiclaw.cli.device.GatewayClient") as mock_cls:
        mock_cls.return_value.list_devices = AsyncMock(return_value={"devices": [], "count": 0})
        r = runner.invoke(cli, ["device", "list"])
    assert r.exit_code == 0
    assert "No devices" in r.output


def test_device_list_json(runner):
    """List with --output json prints JSON."""
    with patch("mobiclaw.cli.device.GatewayClient") as mock_cls:
        mock_cls.return_value.list_devices = AsyncMock(
            return_value={"devices": [{"device_id": "x", "last_heartbeat": "2026-01-01"}], "count": 1}
        )
        r = runner.invoke(cli, ["--output", "json", "device", "list"])
    assert r.exit_code == 0
    assert '"device_id": "x"' in r.output


def test_device_show(runner):
    """Show fetches device and renders."""
    with patch("mobiclaw.cli.device.GatewayClient") as mock_cls:
        mock_cls.return_value.get_device = AsyncMock(
            return_value={
                "device_id": "dev1",
                "device_name": "Pixel 6",
                "tailscale_ip": "100.1.2.3",
                "adb_port": 5555,
                "last_heartbeat": "2026-03-18T10:00:00Z",
                "first_seen": "2026-03-18T09:00:00Z",
            }
        )
        r = runner.invoke(cli, ["device", "show", "dev1"])
    assert r.exit_code == 0
    assert "dev1" in r.output
    assert "Pixel 6" in r.output
    assert "100.1.2.3" in r.output


def test_device_remove_with_yes(runner):
    """Remove with --yes skips confirmation and removes."""
    with patch("mobiclaw.cli.device.GatewayClient") as mock_cls:
        mock_cls.return_value.remove_device = AsyncMock(return_value={"ok": True})
        r = runner.invoke(cli, ["device", "remove", "dev1", "--yes"])
    assert r.exit_code == 0
    assert "Removed" in r.output
    assert "dev1" in r.output


def test_device_remove_with_confirm(runner):
    """Remove without --yes prompts and removes when confirmed."""
    with patch("mobiclaw.cli.device.GatewayClient") as mock_cls:
        mock_cls.return_value.remove_device = AsyncMock(return_value={"ok": True})
        r = runner.invoke(cli, ["device", "remove", "dev1"], input="y\n")
    assert r.exit_code == 0
    assert "Removed" in r.output


def test_device_remove_abort(runner):
    """Remove without --yes aborts when user says no."""
    with patch("mobiclaw.cli.device.GatewayClient") as mock_cls:
        mock_remove = AsyncMock(return_value={"ok": True})
        mock_cls.return_value.remove_device = mock_remove
        r = runner.invoke(cli, ["device", "remove", "dev1"], input="n\n")
    assert r.exit_code == 0
    mock_remove.assert_not_called()


def test_device_help(runner):
    """Device group and subcommands have help."""
    r = runner.invoke(cli, ["device", "--help"])
    assert r.exit_code == 0
    assert "list" in r.output
    assert "show" in r.output
    assert "remove" in r.output
