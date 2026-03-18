# -*- coding: utf-8 -*-
"""设备管理：存储、ADB 连接与生命周期。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import adbutils
from adbutils import adb

logger = logging.getLogger(__name__)

# 设备存储
_DEVICE_STORE: dict[str, dict[str, Any]] = {}
_DEVICE_LOCK = asyncio.Lock()
_DEVICE_STORE_FILE: Path = Path(
    (os.environ.get("MOBICLAW_DEVICE_STORE_PATH") or os.environ.get("SENESCHAL_DEVICE_STORE_PATH") or "").strip()
    or Path.home() / ".mobiclaw" / "devices.json"
)


async def _adb_run(*args: str) -> tuple[int, str]:
    """执行 adb 命令并返回 (returncode, stdout)。

    基于 adbutils 库实现，通过 adb server socket 协议通信，
    无需本地安装 adb 命令行工具。支持子命令：devices / connect / disconnect。
    """
    if not args:
        return 1, "No command specified"

    cmd = args[0]

    def _run_sync() -> tuple[int, str]:
        try:
            if cmd == "devices":
                lines = ["List of devices attached"]
                for info in adb.list(extended=True):
                    lines.append(f"{info.serial}\t{info.state}")
                return 0, "\n".join(lines)
            elif cmd == "connect" and len(args) > 1:
                output = adb.connect(args[1])
                return 0, output
            elif cmd == "disconnect" and len(args) > 1:
                try:
                    adb.disconnect(args[1])
                except adbutils.AdbError:
                    pass
                return 0, f"disconnected {args[1]}"
            else:
                return 1, f"Unsupported adb command: {' '.join(args)}"
        except Exception as e:
            return 1, str(e)

    return await asyncio.to_thread(_run_sync)


async def _ensure_adb_connected(ip: str, port: int) -> None:
    """确保 adb 已连接到 ip:port，端口变化时自动重连。"""
    target = f"{ip}:{port}"

    def _sync_ensure() -> None:

        connected_with_target = False
        stale_targets: list[str] = []

        for info in adb.list(extended=True):
            serial = info.serial
            if serial == target:
                if info.state == "device":
                    connected_with_target = True
                else:
                    stale_targets.append(serial)
            elif serial.startswith(f"{ip}:"):
                stale_targets.append(serial)

        if connected_with_target and not stale_targets:
            logger.debug("ADB already connected to %s", target)
            return

        for old in stale_targets:
            logger.info("ADB disconnecting stale target %s", old)
            try:
                adb.disconnect(old)
            except adbutils.AdbError:
                pass

        logger.info("ADB connecting to %s", target)
        try:
            output = adb.connect(target)
            if "connected" in output.lower():
                logger.info("ADB connected to %s", target)
            else:
                logger.warning("ADB connect to %s may have failed: %s", target, output)
        except Exception as e:
            logger.warning("ADB connect to %s may have failed: %s", target, e)

    await asyncio.to_thread(_sync_ensure)


async def _load_device_store() -> None:
    """从磁盘加载设备信息到内存，并尝试 ADB 连接所有设备。"""
    global _DEVICE_STORE
    if _DEVICE_STORE_FILE.exists():
        try:
            data = json.loads(_DEVICE_STORE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _DEVICE_STORE = data
                logger.info("Loaded %d devices from %s", len(data), _DEVICE_STORE_FILE)
            else:
                logger.warning("Invalid device store format, expected dict, got %s", type(data).__name__)
        except Exception:
            logger.exception("Failed to load device store from %s", _DEVICE_STORE_FILE)

    for device in _DEVICE_STORE.values():
        ip = device.get("tailscale_ip")
        port = device.get("adb_port")
        if ip and port:
            try:
                await _ensure_adb_connected(ip, int(port))
            except Exception:
                logger.warning("ADB connect failed for %s:%s on startup (device may be offline)", ip, port)


def _save_device_store() -> None:
    """将内存中的设备信息持久化到磁盘。"""
    try:
        _DEVICE_STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _DEVICE_STORE_FILE.write_text(
            json.dumps(_DEVICE_STORE, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        logger.exception("Failed to save device store to %s", _DEVICE_STORE_FILE)


async def _disconnect_all_devices() -> None:
    """应用退出时断开所有已注册设备的 ADB 连接。"""

    def _sync_disconnect_all() -> None:
        for device in _DEVICE_STORE.values():
            ip = device.get("tailscale_ip")
            port = device.get("adb_port")
            if ip and port:
                target = f"{ip}:{port}"
                try:
                    logger.info("ADB disconnecting %s on shutdown", target)
                    adb.disconnect(target)
                except Exception:
                    logger.warning("Failed to disconnect %s on shutdown", target)

    await asyncio.to_thread(_sync_disconnect_all)
