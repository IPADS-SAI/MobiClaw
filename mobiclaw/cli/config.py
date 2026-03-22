"""CLI configuration loading and saving."""
from pathlib import Path

import click
import yaml

_DEFAULTS = {
    "server_url": "http://localhost:8090",
    "api_key": "",
    "default_output": "table",
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


def resolve_config(ctx: click.Context) -> dict:
    """Merge ctx.obj overrides with load_cli_config(). Priority: ctx.obj > config file > defaults."""
    ctx.ensure_object(dict)
    file_cfg = load_cli_config()
    return {
        "server_url": ctx.obj.get("server_url") or file_cfg.get("server_url") or "http://localhost:8090",
        "api_key": ctx.obj.get("api_key") or file_cfg.get("api_key") or "",
        "output_fmt": ctx.obj.get("output_fmt") or file_cfg.get("default_output") or "table",
        "verbose": ctx.obj.get("verbose", False),
    }


def register_config_commands(cli_group):
    """Register config subcommand group on the root CLI."""

    @cli_group.group("config")
    def config_cmd():
        """Manage CLI configuration."""

    @config_cmd.command("show")
    def config_show():
        """Load config, print path and all key-value pairs."""
        cfg = load_cli_config()
        click.echo(f"Config file: {get_config_path()}")
        for k, v in cfg.items():
            click.echo(f"  {k}: {v}")

    @config_cmd.command("set")
    @click.argument("key", type=click.Choice(["server_url", "api_key", "default_output"]))
    @click.argument("value")
    def config_set(key, value):
        """Set config key to value."""
        cfg = load_cli_config()
        cfg[key] = value
        save_cli_config(cfg)
        click.echo(f"Set {key} = {value}")

    @config_cmd.command("reset")
    def config_reset():
        """Reset config to defaults."""
        reset_config()
        click.echo("Config reset to defaults")
