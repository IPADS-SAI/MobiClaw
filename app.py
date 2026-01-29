#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seneschal 应用入口。"""

from __future__ import annotations

import asyncio

from seneschal.workflows import main


if __name__ == "__main__":
    asyncio.run(main())
