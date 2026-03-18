"""Tests for mobiclaw.cli.config."""
import pytest
from pathlib import Path
from click.testing import CliRunner

from mobiclaw.cli.config import load_cli_config, get_config_path
from mobiclaw.cli.main import cli


def test_load_cli_config_returns_defaults_when_no_file():
    cfg = load_cli_config()
    assert cfg["server_url"] == "http://localhost:8090"
    assert cfg["api_key"] == ""
    assert cfg["default_output"] == "table"
    assert cfg["default_mode"] == "chat"


def test_get_config_path():
    p = get_config_path()
    assert "mobiclaw" in str(p)
    assert p.name == "cli.yaml"


def test_config_show(runner=None):
    r = runner or CliRunner()
    result = r.invoke(cli, ["config", "show"])
    assert result.exit_code == 0
    assert "server_url" in result.output
