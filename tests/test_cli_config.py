"""Tests for mobiclaw.cli.config."""
import pytest
from pathlib import Path
from mobiclaw.cli.config import load_cli_config, get_config_path


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
