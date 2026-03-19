"""Tests for mobiclaw.cli.feishu."""
from unittest.mock import AsyncMock, patch


import pytest
from click.testing import CliRunner

from mobiclaw.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_feishu_send_event(runner, tmp_path):
    """feishu send-event reads JSON, POSTs to gateway, prints response."""
    payload_file = tmp_path / "event.json"
    payload_file.write_text('{"type": "message", "data": {"text": "hello"}}')

    with patch("mobiclaw.cli.feishu.GatewayClient") as mock_cls:
        mock_cls.return_value.send_feishu_event = AsyncMock(
            return_value={"ok": True, "event_id": "evt-123"}
        )
        r = runner.invoke(cli, ["feishu", "send-event", str(payload_file)])
    assert r.exit_code == 0
    mock_cls.return_value.send_feishu_event.assert_called_once_with(
        {"type": "message", "data": {"text": "hello"}}
    )
    assert "evt-123" in r.output or "ok" in r.output.lower()


def test_feishu_send_event_invalid_json(runner, tmp_path):
    """feishu send-event with invalid JSON raises error."""
    payload_file = tmp_path / "bad.json"
    payload_file.write_text("{invalid json")

    r = runner.invoke(cli, ["feishu", "send-event", str(payload_file)])
    assert r.exit_code != 0
    assert "Invalid JSON" in r.output or "JSON" in r.output


def test_feishu_send_event_file_not_found(runner):
    """feishu send-event with missing file raises error."""
    r = runner.invoke(cli, ["feishu", "send-event", "/nonexistent/path.json"])
    assert r.exit_code != 0


def test_feishu_help(runner):
    """feishu group and subcommands have help."""
    r = runner.invoke(cli, ["feishu", "--help"])
    assert r.exit_code == 0
    assert "send-event" in r.output


def test_feishu_send_event_json_output(runner, tmp_path):
    """feishu send-event with --output json prints JSON."""
    payload_file = tmp_path / "event.json"
    payload_file.write_text('{"type": "test"}')

    with patch("mobiclaw.cli.feishu.GatewayClient") as mock_cls:
        mock_cls.return_value.send_feishu_event = AsyncMock(
            return_value={"ok": True, "event_id": "evt-456"}
        )
        r = runner.invoke(
            cli, ["--output", "json", "feishu", "send-event", str(payload_file)]
        )
    assert r.exit_code == 0
    assert "evt-456" in r.output
    assert "ok" in r.output
