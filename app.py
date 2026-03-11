#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seneschal 应用入口。"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path


def _load_env_file(env_path: Path) -> None:
    """Load key/value pairs from a .env file without external dependencies."""
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(Path(__file__).with_name(".env"))

from seneschal.workflows import main

# logging.basicConfig(
#     level=os.environ.get("SENESCHAL_LOG_LEVEL", "INFO"),
#     format="%(asctime)s-%(levelname)s-%(name)s : %(message)s",
# )
logging.basicConfig(
    level=os.environ.get("SENESCHAL_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s : %(message)s",
)


if __name__ == "__main__":
    asyncio.run(main())
