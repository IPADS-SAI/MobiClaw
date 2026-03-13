# -*- coding: utf-8 -*-
"""Environment loading helpers."""

from __future__ import annotations

import os
from pathlib import Path


def _strip_inline_comment(value: str) -> str:
    """Strip inline comments while preserving # inside quotes."""
    in_single = False
    in_double = False
    escaped = False
    out: list[str] = []

    for ch in value:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            out.append(ch)
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
            continue
        if ch == "#" and not in_single and not in_double:
            break
        out.append(ch)
    return "".join(out).strip()


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
    value = _strip_inline_comment(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
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
