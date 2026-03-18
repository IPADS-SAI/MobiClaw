"""Tests for mobiclaw.cli.file."""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from mobiclaw.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_file_download_default_output(runner, tmp_path, monkeypatch):
    """Download uses cwd/name when --output not given."""
    monkeypatch.chdir(tmp_path)
    with patch("mobiclaw.cli.file.GatewayClient") as mock_cls:
        mock_cls.return_value.download_file = AsyncMock(return_value=None)
        r = runner.invoke(cli, ["file", "download", "job-123", "report.pdf"])
    assert r.exit_code == 0
    mock_cls.return_value.download_file.assert_called_once()
    call_args = mock_cls.return_value.download_file.call_args
    assert call_args[0][0] == "job-123"
    assert call_args[0][1] == "report.pdf"
    assert call_args[0][2] == tmp_path / "report.pdf"
    assert "Saved to" in r.output
    assert "report.pdf" in r.output


def test_file_download_custom_output(runner, tmp_path):
    """Download uses --output path when given."""
    out = tmp_path / "custom" / "output.txt"
    with patch("mobiclaw.cli.file.GatewayClient") as mock_cls:
        mock_cls.return_value.download_file = AsyncMock(return_value=None)
        r = runner.invoke(
            cli,
            ["file", "download", "job-456", "data.csv", "--output", str(out)],
        )
    assert r.exit_code == 0
    mock_cls.return_value.download_file.assert_called_once()
    call_args = mock_cls.return_value.download_file.call_args
    assert call_args[0][0] == "job-456"
    assert call_args[0][1] == "data.csv"
    assert call_args[0][2] == out
    assert "Saved to" in r.output


def test_file_download_help(runner):
    """File group and download subcommand have help."""
    r = runner.invoke(cli, ["file", "--help"])
    assert r.exit_code == 0
    assert "download" in r.output
    r = runner.invoke(cli, ["file", "download", "--help"])
    assert r.exit_code == 0
    assert "JOB_ID" in r.output
    assert "NAME" in r.output
    assert "--output" in r.output
