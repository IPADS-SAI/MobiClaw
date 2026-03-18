"""Tests for mobiclaw.cli.mcp."""
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from mobiclaw.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_mcp_list(runner):
    """mcp list renders servers table."""
    with patch("mobiclaw.cli.mcp.GatewayClient") as mock_cls:
        mock_cls.return_value.list_mcp_servers = AsyncMock(
            return_value={
                "servers": [
                    {"name": "fs", "transport": "stdio", "status": "connected", "tools": ["read_file", "write_file"]},
                    {"name": "web", "transport": "sse", "status": "connecting", "tools": []},
                ],
                "enabled": True,
            }
        )
        r = runner.invoke(cli, ["mcp", "list"])
    assert r.exit_code == 0
    assert "fs" in r.output
    assert "stdio" in r.output
    assert "sse" in r.output
    assert "read_file" in r.output or "write_file" in r.output


def test_mcp_list_empty(runner):
    """mcp list with no servers."""
    with patch("mobiclaw.cli.mcp.GatewayClient") as mock_cls:
        mock_cls.return_value.list_mcp_servers = AsyncMock(
            return_value={"servers": [], "enabled": False}
        )
        r = runner.invoke(cli, ["mcp", "list"])
    assert r.exit_code == 0


def test_mcp_add_stdio(runner):
    """mcp add with command uses stdio transport."""
    with patch("mobiclaw.cli.mcp.GatewayClient") as mock_cls:
        add_mock = AsyncMock(return_value={"ok": True, "name": "fs", "status": "connecting"})
        mock_cls.return_value.add_mcp_server = add_mock
        r = runner.invoke(
            cli,
            [
                "mcp", "add", "fs", "npx",
                "--args", "-y", "--args", "@modelcontextprotocol/server-filesystem",
                "--env", "ROOT=/tmp",
            ],
        )
    assert r.exit_code == 0
    add_mock.assert_called_once()
    body = add_mock.call_args[0][0]
    assert body["name"] == "fs"
    assert body["transport"] == "stdio"
    assert body["command"] == "npx"
    assert body["args"] == ["-y", "@modelcontextprotocol/server-filesystem"]
    assert body["env"] == {"ROOT": "/tmp"}


def test_mcp_add_sse(runner):
    """mcp add with --url uses sse transport."""
    with patch("mobiclaw.cli.mcp.GatewayClient") as mock_cls:
        add_mock = AsyncMock(return_value={"ok": True, "name": "web", "status": "connecting"})
        mock_cls.return_value.add_mcp_server = add_mock
        r = runner.invoke(cli, ["mcp", "add", "web", "--url", "http://localhost:3000/sse"])
    assert r.exit_code == 0
    add_mock.assert_called_once()
    body = add_mock.call_args[0][0]
    assert body["name"] == "web"
    assert body["transport"] == "sse"
    assert body["url"] == "http://localhost:3000/sse"


def test_mcp_add_neither_command_nor_url(runner):
    """mcp add without command or --url raises usage error."""
    r = runner.invoke(cli, ["mcp", "add", "foo"])
    assert r.exit_code != 0
    assert "command" in r.output.lower() or "url" in r.output.lower()


def test_mcp_remove_with_yes(runner):
    """mcp remove --yes skips confirmation."""
    with patch("mobiclaw.cli.mcp.GatewayClient") as mock_cls:
        mock_cls.return_value.remove_mcp_server = AsyncMock(
            return_value={"ok": True, "name": "fs", "status": "removed"}
        )
        r = runner.invoke(cli, ["mcp", "remove", "fs", "--yes"])
    assert r.exit_code == 0
    mock_cls.return_value.remove_mcp_server.assert_called_once_with("fs")


def test_mcp_remove_without_yes_aborts_on_no(runner):
    """mcp remove without --yes aborts when user says no."""
    with patch("mobiclaw.cli.mcp.click.confirm", return_value=False):
        r = runner.invoke(cli, ["mcp", "remove", "fs"], input="n\n")
    assert r.exit_code != 0


def test_mcp_remove_without_yes_proceeds_on_yes(runner):
    """mcp remove without --yes proceeds when user confirms."""
    with patch("mobiclaw.cli.mcp.GatewayClient") as mock_cls:
        mock_cls.return_value.remove_mcp_server = AsyncMock(
            return_value={"ok": True, "name": "fs", "status": "removed"}
        )
        with patch("mobiclaw.cli.mcp.click.confirm", return_value=True):
            r = runner.invoke(cli, ["mcp", "remove", "fs"])
    assert r.exit_code == 0
    mock_cls.return_value.remove_mcp_server.assert_called_once_with("fs")


def test_mcp_help(runner):
    """mcp group and subcommands have help."""
    r = runner.invoke(cli, ["mcp", "--help"])
    assert r.exit_code == 0
    assert "list" in r.output
    assert "add" in r.output
    assert "remove" in r.output
