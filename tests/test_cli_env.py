"""Tests for mobiclaw.cli.env."""
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from mobiclaw.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_env_show(runner):
    """env show renders content and variables."""
    with patch("mobiclaw.cli.env.GatewayClient") as mock_cls:
        mock_cls.return_value.get_env = AsyncMock(
            return_value={
                "path": "/path/to/.env",
                "content": "OPENROUTER_API_KEY=sk-xxx\n",
                "variables": {"OPENROUTER_API_KEY": "sk-xxx"},
            }
        )
        r = runner.invoke(cli, ["env", "show"])
    assert r.exit_code == 0
    assert "OPENROUTER_API_KEY" in r.output
    assert "sk-xxx" in r.output


def test_env_show_schema(runner):
    """env show --schema renders values and unmanaged."""
    with patch("mobiclaw.cli.env.GatewayClient") as mock_cls:
        mock_cls.return_value.get_env_schema = AsyncMock(
            return_value={
                "path": "/path/to/.env",
                "schema": [],
                "values": {"OPENROUTER_API_KEY": "sk-xxx", "MOBICLAW_LOG_LEVEL": "INFO"},
                "unmanaged": {"CUSTOM_X": "1"},
                "variables": {},
                "content": "",
            }
        )
        r = runner.invoke(cli, ["env", "show", "--schema"])
    assert r.exit_code == 0
    assert "OPENROUTER_API_KEY" in r.output
    assert "sk-xxx" in r.output
    assert "MOBICLAW_LOG_LEVEL" in r.output
    assert "INFO" in r.output
    assert "CUSTOM_X" in r.output
    assert "1" in r.output


def test_env_show_json(runner):
    """env show --output json outputs raw JSON."""
    with patch("mobiclaw.cli.env.GatewayClient") as mock_cls:
        mock_cls.return_value.get_env = AsyncMock(
            return_value={"path": ".env", "content": "X=1", "variables": {"X": "1"}}
        )
        r = runner.invoke(cli, ["--output", "json", "env", "show"])
    assert r.exit_code == 0
    assert '"path"' in r.output
    assert '"content"' in r.output
    assert '"variables"' in r.output


def test_env_set_managed_key(runner):
    """env set for managed key calls set_env_structured with values."""
    with patch("mobiclaw.cli.env.GatewayClient") as mock_cls:
        get_mock = AsyncMock(
            return_value={
                "values": {"OPENROUTER_API_KEY": "old"},
                "unmanaged": {},
                "schema": [{"items": [{"key": "OPENROUTER_API_KEY"}]}],
            }
        )
        set_mock = AsyncMock(return_value={"ok": True})
        mock_cls.return_value.get_env_schema = get_mock
        mock_cls.return_value.set_env_structured = set_mock
        r = runner.invoke(cli, ["env", "set", "OPENROUTER_API_KEY", "new-key"])
    assert r.exit_code == 0
    set_mock.assert_called_once()
    args, kwargs = set_mock.call_args
    assert args[0] == {"OPENROUTER_API_KEY": "new-key"}
    assert kwargs.get("preserve_unmanaged") is True
    assert "unmanaged" not in kwargs or kwargs.get("unmanaged") is None


def test_env_set_unmanaged_key(runner):
    """env set for unmanaged key calls set_env_structured with unmanaged."""
    with patch("mobiclaw.cli.env.GatewayClient") as mock_cls:
        get_mock = AsyncMock(
            return_value={
                "values": {"OPENROUTER_API_KEY": "x"},
                "unmanaged": {"CUSTOM_A": "a"},
                "schema": [{"items": [{"key": "OPENROUTER_API_KEY"}]}],
            }
        )
        set_mock = AsyncMock(return_value={"ok": True})
        mock_cls.return_value.get_env_schema = get_mock
        mock_cls.return_value.set_env_structured = set_mock
        r = runner.invoke(cli, ["env", "set", "CUSTOM_B", "b"])
    assert r.exit_code == 0
    set_mock.assert_called_once()
    args, kwargs = set_mock.call_args
    assert args[0] == {"OPENROUTER_API_KEY": "x"}
    assert kwargs.get("unmanaged") == {"CUSTOM_A": "a", "CUSTOM_B": "b"}
    assert kwargs.get("preserve_unmanaged") is False


def test_env_edit(runner):
    """env edit gets env, opens editor, sets content."""
    with patch("mobiclaw.cli.env.GatewayClient") as mock_cls:
        get_mock = AsyncMock(
            return_value={"path": ".env", "content": "X=1\n", "variables": {"X": "1"}}
        )
        set_mock = AsyncMock(return_value={"ok": True, "path": ".env"})
        mock_cls.return_value.get_env = get_mock
        mock_cls.return_value.set_env_content = set_mock

        def fake_run(cmd, **kwargs):
            # Simulate editor: write modified content to the temp file
            import os
            path = cmd[-1]
            with open(path, "w") as f:
                f.write("X=2\n")

        with patch("mobiclaw.cli.env.subprocess.run", side_effect=fake_run):
            r = runner.invoke(cli, ["env", "edit"])
    assert r.exit_code == 0
    set_mock.assert_called_once_with("X=2\n")


def test_env_edit_calls_editor(runner):
    """env edit invokes editor on temp file and saves modified content."""
    with patch("mobiclaw.cli.env.GatewayClient") as mock_cls:
        mock_cls.return_value.get_env = AsyncMock(
            return_value={"path": ".env", "content": "", "variables": {}}
        )
        set_mock = AsyncMock(return_value={"ok": True})
        mock_cls.return_value.set_env_content = set_mock

        def capture_run(cmd, **kwargs):
            path = cmd[-1]
            with open(path, "w") as f:
                f.write("Y=edited")

        with patch("mobiclaw.cli.env.subprocess.run", side_effect=capture_run):
            r = runner.invoke(cli, ["env", "edit"])
    assert r.exit_code == 0
    set_mock.assert_called_once_with("Y=edited")


def test_env_help(runner):
    """env group and subcommands have help."""
    r = runner.invoke(cli, ["env", "--help"])
    assert r.exit_code == 0
    assert "show" in r.output
    assert "set" in r.output
    assert "edit" in r.output
