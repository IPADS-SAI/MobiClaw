"""Tests for mobiclaw.cli.session."""
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from mobiclaw.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_session_list(runner):
    """List sessions renders table."""
    with patch("mobiclaw.cli.session.GatewayClient") as mock_cls:
        mock_cls.return_value.list_sessions = AsyncMock(
            return_value={
                "sessions": [
                    {
                        "context_id": "ctx1",
                        "session_id": "ctx1",
                        "dir_name": "20260312_120000_000001-chat_ctx1",
                        "updated_at": "2026-03-12T12:00:00Z",
                        "path": "/data/sessions/20260312_120000_000001-chat_ctx1",
                    },
                ]
            }
        )
        r = runner.invoke(cli, ["session", "list"])
    assert r.exit_code == 0
    assert "ctx1" in r.output
    assert "20260312" in r.output  # dir_name prefix (may be truncated in table)


def test_session_list_empty(runner):
    """List with no sessions prints message."""
    with patch("mobiclaw.cli.session.GatewayClient") as mock_cls:
        mock_cls.return_value.list_sessions = AsyncMock(return_value={"sessions": []})
        r = runner.invoke(cli, ["session", "list"])
    assert r.exit_code == 0
    assert "No sessions" in r.output


def test_session_list_json(runner):
    """List with --output json prints JSON."""
    with patch("mobiclaw.cli.session.GatewayClient") as mock_cls:
        mock_cls.return_value.list_sessions = AsyncMock(
            return_value={"sessions": [{"context_id": "x", "updated_at": "2026-01-01"}]}
        )
        r = runner.invoke(cli, ["--output", "json", "session", "list"])
    assert r.exit_code == 0
    assert '"context_id": "x"' in r.output


def test_session_show(runner):
    """Show fetches session and renders messages."""
    with patch("mobiclaw.cli.session.GatewayClient") as mock_cls:
        mock_cls.return_value.get_session = AsyncMock(
            return_value={
                "context_id": "ctx1",
                "session_id": "ctx1",
                "summary": {
                    "context_id": "ctx1",
                    "message_count": 2,
                    "path": "/data/sessions/ctx1",
                },
                "messages": [
                    {"role": "user", "name": "user", "text": "Hello", "ts": "2026-03-12T10:00:00Z"},
                    {"role": "assistant", "name": "assistant", "text": "Hi there", "ts": "2026-03-12T10:00:01Z"},
                ],
            }
        )
        r = runner.invoke(cli, ["session", "show", "ctx1"])
    assert r.exit_code == 0
    assert "Hello" in r.output
    assert "Hi there" in r.output
    assert "user" in r.output
    assert "assistant" in r.output


def test_session_show_with_limit(runner):
    """Show passes --limit to get_session."""
    with patch("mobiclaw.cli.session.GatewayClient") as mock_cls:
        mock_get = AsyncMock(return_value={"messages": [], "summary": {}})
        mock_cls.return_value.get_session = mock_get
        r = runner.invoke(cli, ["session", "show", "ctx1", "--limit", "50"])
    assert r.exit_code == 0
    mock_get.assert_called_once_with("ctx1", limit=50)


def test_session_delete_with_yes(runner):
    """Delete with --yes skips confirmation and deletes."""
    with patch("mobiclaw.cli.session.GatewayClient") as mock_cls:
        mock_cls.return_value.delete_session = AsyncMock(
            return_value={"ok": True, "context_id": "ctx1", "deleted": 1}
        )
        r = runner.invoke(cli, ["session", "delete", "ctx1", "--yes"])
    assert r.exit_code == 0
    assert "Deleted" in r.output
    assert "ctx1" in r.output


def test_session_delete_with_confirm(runner):
    """Delete without --yes prompts and deletes when confirmed."""
    with patch("mobiclaw.cli.session.GatewayClient") as mock_cls:
        mock_cls.return_value.delete_session = AsyncMock(
            return_value={"ok": True, "context_id": "ctx1", "deleted": 1}
        )
        r = runner.invoke(cli, ["session", "delete", "ctx1"], input="y\n")
    assert r.exit_code == 0
    assert "Deleted" in r.output


def test_session_delete_abort(runner):
    """Delete without --yes aborts when user says no."""
    with patch("mobiclaw.cli.session.GatewayClient") as mock_cls:
        mock_delete = AsyncMock(return_value={"ok": True})
        mock_cls.return_value.delete_session = mock_delete
        r = runner.invoke(cli, ["session", "delete", "ctx1"], input="n\n")
    assert r.exit_code == 0
    mock_delete.assert_not_called()


def test_session_help(runner):
    """Session group and subcommands have help."""
    r = runner.invoke(cli, ["session", "--help"])
    assert r.exit_code == 0
    assert "list" in r.output
    assert "show" in r.output
    assert "delete" in r.output
