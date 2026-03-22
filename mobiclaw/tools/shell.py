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

_BLOCKED_OPERATORS = ("|", ";", "&&", "||", ">", ">>", "<", "<<")


def _load_allowlist() -> set[str]:
    raw = os.environ.get(
        "MOBICLAW_SHELL_ALLOWLIST",
        "ls,rg,grep,cat,head,tail,sed,awk,find,whoami,uname,date,pwd,mkdir,git,python,python3,cd,wget,curl,echo,node,npm,java,javac,pip",
    )
    return {item.strip() for item in raw.split(",") if item.strip()}


def _find_unsafe_tokens(args: list[str]) -> list[dict[str, str]]:
    """Return unsafe shell-like tokens found in parsed args.

    We intentionally validate post-shlex tokens to avoid false positives,
    e.g. URL query values containing ">".
    """
    if not args:
        return []

    unsafe: list[dict[str, str]] = []
    for arg in args:
        if arg in _BLOCKED_OPERATORS:
            unsafe.append({"token": arg, "reason": "control_operator"})
            continue

        # Block command-substitution-like patterns.
        if "`" in arg or "$(" in arg:
            unsafe.append({"token": arg, "reason": "command_substitution"})

    return unsafe


def _format_allowlist(allowlist: set[str]) -> str:
    return ", ".join(sorted(allowlist)) if allowlist else "<empty>"


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
    """Run a safe local shell command with allowlist enforcement.

    Args:
        command: Command line string to execute.
    """
    command = (command or "").strip()
    if not command:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Shell] Empty command.")],
            metadata={"error": "empty_command"},
        )

    try:
        args = shlex.split(command)
    except ValueError as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Shell] Parse error: {exc}")],
            metadata={"error": "parse_error", "command": command},
        )

    if not args:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Shell] No command tokens found.")],
            metadata={"error": "empty_command_tokens", "command": command},
        )

    unsafe_tokens = _find_unsafe_tokens(args)
    if unsafe_tokens:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "[Shell] Command contains unsafe tokens. "
                        f"Found: {', '.join(item['token'] for item in unsafe_tokens)}. "
                        f"Blocked operator tokens: {', '.join(_BLOCKED_OPERATORS)}. "
                        "Shell features like pipes, chaining, redirection, and command substitution are not allowed. "
                        "Use a single simple command."
                    ),
                )
            ],
            metadata={
                "error": "unsafe_tokens",
                "command": command,
                "unsafe_tokens": unsafe_tokens,
                "blocked_operator_tokens": list(_BLOCKED_OPERATORS),
            },
        )

    allowlist = _load_allowlist()
    if allowlist and args[0] not in allowlist:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"[Shell] Command not allowed: {args[0]}. "
                        f"Allowed commands: {_format_allowlist(allowlist)}. "
                        "Update MOBICLAW_SHELL_ALLOWLIST to permit it."
                    ),
                )
            ],
            metadata={
                "error": "command_not_allowed",
                "command": command,
                "requested_command": args[0],
                "allowed_commands": sorted(allowlist),
            },
        )

    args = _expand_glob_args(args)

    timeout_s = float(os.environ.get("MOBICLAW_SHELL_TIMEOUT", "20"))
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
            metadata={"error": "runtime_not_found", "command": command, "runtime": args[0]},
        )
    except subprocess.TimeoutExpired:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Shell] Command timed out.")],
            metadata={"error": "timeout", "command": command, "timeout_s": timeout_s},
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
