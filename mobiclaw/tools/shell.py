# -*- coding: utf-8 -*-
"""Safe local shell command tool."""

from __future__ import annotations

import logging
import os
import glob
import shlex
import subprocess
from pathlib import Path

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

logger = logging.getLogger(__name__)

_BLOCKED_OPERATORS = ("|", ";", "||")
_MAX_CHAIN_SEGMENTS = 2


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


def _split_command_chain(command: str, *, max_segments: int = _MAX_CHAIN_SEGMENTS) -> tuple[list[str] | None, str | None]:
    raw = (command or "").strip()
    if not raw:
        return None, "empty_command"

    segments = [part.strip() for part in raw.split("&&")]
    if any(not segment for segment in segments):
        return None, "invalid_chain_syntax"
    if len(segments) > max_segments:
        return None, "invalid_chain_length"
    return segments, None


async def run_shell_command(command: str) -> ToolResponse:
    """Run a safe local shell command with allowlist enforcement.

    Args:
        command: Command line string to execute, without quotation marks. Operators like "|", ";", "||" are also not allowed for safety reasons.
    """
    command = (command or "").strip()
    if not command:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Shell] Empty command.")],
            metadata={"error": "empty_command"},
        )

    command_segments, chain_error = _split_command_chain(command)
    if chain_error or not command_segments:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Shell] Invalid command chain: {chain_error}")],
            metadata={
                "error": chain_error or "invalid_command_chain",
                "command": command,
                "max_chain_segments": _MAX_CHAIN_SEGMENTS,
            },
        )

    allowlist = _load_allowlist()
    parsed_segments: list[list[str]] = []
    for idx, segment in enumerate(command_segments, start=1):
        try:
            args = shlex.split(segment)
        except ValueError as exc:
            return ToolResponse(
                content=[TextBlock(type="text", text=f"[Shell] Parse error in segment {idx}: {exc}")],
                metadata={"error": "parse_error", "command": command, "segment_index": idx, "segment_command": segment},
            )

        if not args:
            return ToolResponse(
                content=[TextBlock(type="text", text=f"[Shell] No command tokens found in segment {idx}.")],
                metadata={"error": "empty_command_tokens", "command": command, "segment_index": idx, "segment_command": segment},
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
                            "Shell features like pipes, chaining with unsupported operators, redirection, and command substitution are not allowed."
                        ),
                    )
                ],
                metadata={
                    "error": "unsafe_tokens",
                    "command": command,
                    "segment_index": idx,
                    "segment_command": segment,
                    "unsafe_tokens": unsafe_tokens,
                    "blocked_operator_tokens": list(_BLOCKED_OPERATORS),
                },
            )

        if allowlist and args[0] not in allowlist:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"[Shell] Command not allowed in segment {idx}: {args[0]}. "
                            f"Allowed commands: {_format_allowlist(allowlist)}. "
                            "Update MOBICLAW_SHELL_ALLOWLIST to permit it."
                        ),
                    )
                ],
                metadata={
                    "error": "command_not_allowed",
                    "command": command,
                    "segment_index": idx,
                    "segment_command": segment,
                    "requested_command": args[0],
                    "allowed_commands": sorted(allowlist),
                },
            )

        parsed_segments.append(_expand_glob_args(args))

    timeout_s = float(os.environ.get("MOBICLAW_SHELL_TIMEOUT", "20"))
    logger.info("shell.run command=%s", command)

    segment_results: list[dict[str, object]] = []
    overall_returncode = 0
    current_cwd = Path.cwd()
    for idx, args in enumerate(parsed_segments, start=1):
        segment_command = command_segments[idx - 1]

        if args[0] == "cd":
            if len(args) > 2:
                return ToolResponse(
                    content=[TextBlock(type="text", text=f"[Shell] Invalid cd usage in segment {idx}: too many arguments.")],
                    metadata={
                        "error": "invalid_cd_usage",
                        "command": command,
                        "segment_index": idx,
                        "segment_command": segment_command,
                    },
                )

            target_arg = args[1] if len(args) == 2 else os.path.expanduser("~")
            target_path = Path(os.path.expanduser(target_arg))
            if not target_path.is_absolute():
                target_path = current_cwd / target_path
            target_path = target_path.resolve()

            if not target_path.exists() or not target_path.is_dir():
                return ToolResponse(
                    content=[TextBlock(type="text", text=f"[Shell] cd failed in segment {idx}: no such directory: {target_arg}")],
                    metadata={
                        "error": "cd_directory_not_found",
                        "command": command,
                        "segment_index": idx,
                        "segment_command": segment_command,
                        "requested_path": target_arg,
                    },
                )

            current_cwd = target_path
            segment_results.append(
                {
                    "index": idx,
                    "command": segment_command,
                    "command_args": args,
                    "returncode": 0,
                    "stdout_tail": "",
                    "stderr_tail": "",
                    "cwd": str(current_cwd),
                    "builtin": "cd",
                }
            )
            continue

        try:
            proc = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=str(current_cwd),
            )
        except FileNotFoundError:
            return ToolResponse(
                content=[TextBlock(type="text", text=f"[Shell] Command not found: {args[0]}")],
                metadata={
                    "error": "runtime_not_found",
                    "command": command,
                    "segment_index": idx,
                    "segment_command": segment_command,
                    "runtime": args[0],
                },
            )
        except subprocess.TimeoutExpired:
            return ToolResponse(
                content=[TextBlock(type="text", text="[Shell] Command timed out.")],
                metadata={
                    "error": "timeout",
                    "command": command,
                    "segment_index": idx,
                    "segment_command": segment_command,
                    "timeout_s": timeout_s,
                },
            )

        stdout = (proc.stdout or "").strip()[:4000]
        stderr = (proc.stderr or "").strip()[:2000]
        rc = int(proc.returncode)
        segment_results.append(
            {
                "index": idx,
                "command": segment_command,
                "command_args": args,
                "returncode": rc,
                "stdout_tail": stdout,
                "stderr_tail": stderr,
            }
        )
        overall_returncode = rc
        if rc != 0:
            break

    logger.info("shell.result returncode=%d command=%s", overall_returncode, command)

    message = f"[Shell] Exit code: {overall_returncode}"
    message += f"\n[command] {command}"
    for item in segment_results:
        idx = int(item.get("index") or 0)
        message += f"\n[segment:{idx}] {str(item.get('command') or '')}"
        message += f"\n[segment:{idx}:exit_code] {int(item.get('returncode') or 0)}"
        stdout = str(item.get("stdout_tail") or "")
        stderr = str(item.get("stderr_tail") or "")
        if stdout:
            message += f"\n[segment:{idx}:stdout]\n{stdout}"
        if stderr:
            message += f"\n[segment:{idx}:stderr]\n{stderr}"
        cwd = str(item.get("cwd") or "")
        if cwd:
            message += f"\n[segment:{idx}:cwd] {cwd}"

    metadata: dict[str, object] = {
        "returncode": overall_returncode,
        "segment_count": len(command_segments),
        "segments": segment_results,
    }
    if overall_returncode != 0:
        metadata["error"] = "shell_command_failed"

    return ToolResponse(
        content=[TextBlock(type="text", text=message)],
        metadata=metadata,
    )
