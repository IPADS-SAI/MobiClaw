#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MobiClaw 应用入口。"""

from __future__ import annotations

import asyncio
import logging
import os

from mobiclaw.env import load_project_env

load_project_env()

from mobiclaw.workflows import main

# logging.basicConfig(
#     level=os.environ.get("MOBICLAW_LOG_LEVEL", "INFO"),
#     format="%(asctime)s-%(levelname)s-%(name)s : %(message)s",
# )
logging.basicConfig(
    level=os.environ.get("MOBICLAW_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s : %(message)s",
)


if __name__ == "__main__":
    asyncio.run(main())
