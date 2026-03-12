# -*- coding: utf-8 -*-
"""Environment loading helpers."""

from __future__ import annotations

import os
from pathlib import Path


def _parse_env_line(raw_line: str) -> tuple[str, str] | None:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export "):].strip()
    if "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip().strip('"').strip("'")
    if not key:
        return None
    return key, value


def load_env_file(env_path: Path, *, override: bool = False) -> None:
    """Load key/value pairs from a .env file into process env."""
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(raw_line)
        if not parsed:
            continue
        key, value = parsed
        if override or key not in os.environ:
            os.environ[key] = value


def load_project_env(*, override: bool = False) -> Path:
    """Load `<repo-root>/.env` into process env."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_env_file(env_path, override=override)
    return env_path

