# -*- coding: utf-8 -*-
"""Safe local shell command tool."""

from __future__ import annotations

import logging
import os
import glob
import shlex
import subprocess

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

logger = logging.getLogger(__name__)


def _load_allowlist() -> set[str]:
    raw = os.environ.get(
        "SENESCHAL_SHELL_ALLOWLIST",
        "ls,rg,grep,cat,head,tail,sed,awk,find,whoami,uname,date,pwd,mkdir,git,python,python3,cd,wget,curl,echo",
    )
    return {item.strip() for item in raw.split(",") if item.strip()}


def _has_unsafe_tokens(args: list[str]) -> bool:
    """Check parsed args for shell-like control operators.

    We intentionally validate post-shlex tokens to avoid false positives,
    e.g. URL query values containing ">".
    """
    if not args:
        return False

    # Block explicit shell control operators and redirections.
    blocked_ops = {"|", ";", "&&", "||", ">", ">>", "<", "<<"}
    for arg in args:
        if arg in blocked_ops:
            return True

        # Block command-substitution-like patterns.
        if "`" in arg or "$(" in arg:
            return True

    return False


def _expand_glob_args(args: list[str]) -> list[str]:
    """Expand wildcard tokens without invoking a shell."""
    expanded: list[str] = []
    for arg in args:
        if any(ch in arg for ch in ["*", "?", "["]):
            matches = glob.glob(arg)
            if matches:
                expanded.extend(matches)
            else:
                expanded.append(arg)
            continue
        expanded.append(arg)
    return expanded


async def run_shell_command(command: str) -> ToolResponse:
    """Run a safe local shell command with allowlist enforcement."""
    command = (command or "").strip()
    if not command:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Shell] Empty command.")],
        )

    try:
        args = shlex.split(command)
    except ValueError as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Shell] Parse error: {exc}")],
        )

    if not args:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Shell] No command tokens found.")],
        )

    if _has_unsafe_tokens(args):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="[Shell] Command contains unsafe tokens. Use a single simple command.",
                )
            ],
        )

    allowlist = _load_allowlist()
    if allowlist and args[0] not in allowlist:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "[Shell] Command not allowed. "
                        "Update SENESCHAL_SHELL_ALLOWLIST to permit it."
                    ),
                )
            ],
            metadata={"command": args[0]},
        )

    args = _expand_glob_args(args)

    timeout_s = float(os.environ.get("SENESCHAL_SHELL_TIMEOUT", "20"))
    logger.info("shell.run command=%s", command)

    try:
        proc = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except FileNotFoundError:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Shell] Command not found: {args[0]}")],
        )
    except subprocess.TimeoutExpired:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Shell] Command timed out.")],
        )

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    stdout = stdout[:4000]
    stderr = stderr[:2000]
    logger.info("shell.result returncode=%d command=%s", proc.returncode, command)

    message = f"[Shell] Exit code: {proc.returncode}"
    if stdout:
        message += f"\n[stdout]\n{stdout}"
    if stderr:
        message += f"\n[stderr]\n{stderr}"

    return ToolResponse(
        content=[TextBlock(type="text", text=message)],
        metadata={"returncode": proc.returncode},
    )
