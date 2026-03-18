"""CLI configuration loading and saving."""
from pathlib import Path

import yaml

_DEFAULTS = {
    "server_url": "http://localhost:8090",
    "api_key": "",
    "default_output": "table",
    "default_mode": "chat",
}


def get_config_path() -> Path:
    """Return path to CLI config file."""
    return Path.home() / ".mobiclaw" / "cli.yaml"


def load_cli_config() -> dict:
    """Load config from file, merging with defaults."""
    cfg = _DEFAULTS.copy()
    path = get_config_path()
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            for k in _DEFAULTS:
                if k in data:
                    cfg[k] = data[k]
    return cfg


def save_cli_config(cfg: dict) -> None:
    """Write config to YAML file."""
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False)


def reset_config() -> None:
    """Delete config file if it exists."""
    path = get_config_path()
    if path.exists():
        path.unlink()
