"""Tests for mobiclaw.cli.schedule."""
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from mobiclaw.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_schedule_list(runner):
    """List renders schedules as table."""
    with patch("mobiclaw.cli.schedule.GatewayClient") as mock_cls:
        mock_cls.return_value.list_schedules = AsyncMock(
            return_value={
                "schedules": [
                    {"schedule_id": "s1", "core_task": "hello", "schedule_type": "once"},
                    {"schedule_id": "s2", "core_task": "world", "schedule_type": "cron"},
                ],
                "enabled": True,
            }
        )
        r = runner.invoke(cli, ["schedule", "list"])
    assert r.exit_code == 0
    assert "s1" in r.output
    assert "s2" in r.output
    assert "hello" in r.output
    assert "world" in r.output


def test_schedule_list_empty(runner):
    """List with no schedules shows empty table."""
    with patch("mobiclaw.cli.schedule.GatewayClient") as mock_cls:
        mock_cls.return_value.list_schedules = AsyncMock(
            return_value={"schedules": [], "enabled": True}
        )
        r = runner.invoke(cli, ["schedule", "list"])
    assert r.exit_code == 0


def test_schedule_cancel_with_yes(runner):
    """Cancel with --yes skips confirmation."""
    with patch("mobiclaw.cli.schedule.GatewayClient") as mock_cls:
        mock_cls.return_value.cancel_schedule = AsyncMock(
            return_value={"ok": True, "schedule_id": "s1", "status": "cancelled"}
        )
        r = runner.invoke(cli, ["schedule", "cancel", "s1", "--yes"])
    assert r.exit_code == 0
    assert "Cancelled" in r.output
    assert "s1" in r.output


def test_schedule_cancel_without_yes_confirmed(runner):
    """Cancel without --yes prompts; user confirms."""
    with patch("mobiclaw.cli.schedule.GatewayClient") as mock_cls:
        mock_cls.return_value.cancel_schedule = AsyncMock(
            return_value={"ok": True, "schedule_id": "s1", "status": "cancelled"}
        )
        r = runner.invoke(cli, ["schedule", "cancel", "s1"], input="y\n")
    assert r.exit_code == 0
    assert "Cancelled" in r.output


def test_schedule_cancel_without_yes_aborted(runner):
    """Cancel without --yes prompts; user declines."""
    with patch("mobiclaw.cli.schedule.GatewayClient") as mock_cls:
        mock_cls.return_value.cancel_schedule = AsyncMock()
        r = runner.invoke(cli, ["schedule", "cancel", "s1"], input="n\n")
    assert r.exit_code != 0
    mock_cls.return_value.cancel_schedule.assert_not_called()


def test_schedule_help(runner):
    """Schedule group and subcommands have help."""
    r = runner.invoke(cli, ["schedule", "--help"])
    assert r.exit_code == 0
    assert "list" in r.output
    assert "cancel" in r.output
