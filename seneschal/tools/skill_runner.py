# -*- coding: utf-8 -*-
"""Run skill commands in a specified execution directory."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse


_SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills"
_RUNTIME_NAMES = {"python", "python3", "bash", "sh", "zsh", "node", "npm", "npx", "uv"}


def _is_under_path(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _normalize_runtime(token: str) -> str:
    base = Path(token).name.lower()
    if re.fullmatch(r"python\d+(\.\d+)?", base):
        return "python"
    if base.startswith("python"):
        return "python"
    return base


def _looks_like_script_or_path(token: str) -> bool:
    if token.startswith(("./", "../", "/", "scripts/")):
        return True
    return token.endswith((".py", ".sh", ".js", ".ts"))


def _looks_like_command_line(raw_line: str) -> bool:
    text = raw_line.strip()
    if not text or text.startswith("#"):
        return False
    if text.startswith(("- ", "* ")):
        text = text[2:].strip()
    try:
        tokens = shlex.split(text)
    except ValueError:
        return False
    if not tokens:
        return False
    first = _normalize_runtime(tokens[0])
    if first in _RUNTIME_NAMES:
        return True
    return _looks_like_script_or_path(tokens[0])


def _extract_commands_from_skill_md(skill_md_path: Path) -> list[str]:
    try:
        content = skill_md_path.read_text(encoding="utf-8")
    except OSError:
        return []

    candidates: list[str] = []

    # Extract command-like lines from fenced code blocks.
    fence_pattern = re.compile(r"```(?:bash|sh|shell|zsh|python)?\n(.*?)```", flags=re.DOTALL | re.IGNORECASE)
    for block in fence_pattern.findall(content):
        for line in block.splitlines():
            line = line.strip()
            if _looks_like_command_line(line):
                candidates.append(line)

    # Extract inline code snippets that look like command lines.
    inline_pattern = re.compile(r"`([^`\n]+)`")
    for snippet in inline_pattern.findall(content):
        snippet = snippet.strip()
        if _looks_like_command_line(snippet):
            candidates.append(snippet)

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def _build_command_signature(command: str) -> tuple[str, ...] | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None

    runtime = _normalize_runtime(tokens[0])
    if runtime in {"python", "python3", "uv"}:
        if len(tokens) >= 3 and tokens[1] == "-m":
            return (runtime, "-m", tokens[2])
        if len(tokens) >= 2 and _looks_like_script_or_path(tokens[1]):
            return (runtime, tokens[1])
        return (runtime,)

    if runtime in {"bash", "sh", "zsh", "node", "npm", "npx"}:
        if len(tokens) >= 2:
            return (runtime, tokens[1])
        return (runtime,)

    return (runtime,)


def _is_command_allowed(command: str, allowed_commands: list[str]) -> bool:
    command_sig = _build_command_signature(command)
    if not command_sig:
        return False

    for allowed in allowed_commands:
        allowed_sig = _build_command_signature(allowed)
        if not allowed_sig:
            continue
        if command_sig == allowed_sig:
            return True
    return False


def _resolve_execution_dir(execution_dir: str) -> tuple[Path | None, str | None]:
    raw = (execution_dir or "").strip()
    if not raw:
        return None, "execution_dir_required"

    candidate = Path(raw).expanduser()
    try:
        resolved = candidate.resolve()
    except FileNotFoundError:
        resolved = candidate.absolute()

    if not resolved.exists() or not resolved.is_dir():
        return None, "execution_dir_not_found"
    return resolved, None


async def run_skill_script(
    command: str,
    execution_dir: str,
    timeout_s: float | None = None,
) -> ToolResponse:
    """Run a command in the SKILL.md with a given execution directory.

    Args:
        command: Executable command string.
        execution_dir: Directory to run command in.
        timeout_s: Optional timeout in seconds.
    """
    command = (command or "").strip()
    if not command:
        return ToolResponse(
            content=[TextBlock(type="text", text="[SkillRunner] Empty command")],
            metadata={"error": "empty_command"},
        )

    cwd, cwd_error = _resolve_execution_dir(execution_dir)
    if cwd_error or not cwd:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[SkillRunner] Invalid execution_dir: {cwd_error}")],
            metadata={
                "error": cwd_error or "execution_dir_invalid",
                "execution_dir": execution_dir or "",
            },
        )

    skill_root = _SKILL_ROOT.resolve()
    if not _is_under_path(cwd, skill_root):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"[SkillRunner] execution_dir must be under skill root: {skill_root}",
                ),
            ],
            metadata={
                "error": "execution_dir_not_in_skill_root",
                "execution_dir": str(cwd),
                "skill_root": str(skill_root),
            },
        )

    skill_md_path = cwd / "SKILL.md"
    allowed_commands = _extract_commands_from_skill_md(skill_md_path) if skill_md_path.exists() else []
    if not allowed_commands:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"[SkillRunner] No command whitelist found in: {skill_md_path}",
                ),
            ],
            metadata={
                "error": "skill_whitelist_not_found",
                "execution_dir": str(cwd),
                "skill_md": str(skill_md_path),
            },
        )

    timeout_value = timeout_s
    if timeout_value is None:
        timeout_value = float(os.environ.get("SENESCHAL_SKILL_SCRIPT_TIMEOUT_S", "120"))

    try:
        cmd_args = shlex.split(command)
    except ValueError as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[SkillRunner] Command parse error: {exc}")],
            metadata={"error": "command_parse_error", "command": command},
        )

    if not cmd_args:
        return ToolResponse(
            content=[TextBlock(type="text", text="[SkillRunner] Empty command tokens")],
            metadata={"error": "empty_command_tokens", "command": command},
        )

    if not _is_command_allowed(command, allowed_commands):
        return ToolResponse(
            content=[TextBlock(type="text", text="[SkillRunner] Command is not allowed by SKILL.md whitelist")],
            metadata={
                "error": "script_not_allowed",
                "command": command,
                "execution_dir": str(cwd),
                "skill_md": str(skill_md_path),
                "allowed_command_hints": allowed_commands[:20],
            },
        )

    previous_cwd = Path.cwd()
    os.chdir(cwd)

    try:
        proc = subprocess.run(
            cmd_args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_value,
        )
    except FileNotFoundError:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[SkillRunner] Runtime executable not found: {cmd_args[0]}")],
            metadata={"error": "runtime_not_found", "runtime": cmd_args[0], "command": command},
        )
    except subprocess.TimeoutExpired:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[SkillRunner] Script timeout after {timeout_value:.1f}s")],
            metadata={
                "error": "timeout",
                "timeout_s": timeout_value,
                "command": command,
                "execution_dir": str(cwd),
            },
        )
    finally:
        os.chdir(previous_cwd)

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    stdout_tail = stdout[-4000:]
    stderr_tail = stderr[-2000:]

    message = f"[SkillRunner] Exit code: {proc.returncode}"
    message += f"\n[execution_dir] {cwd}"
    message += f"\n[command] {command}"
    if stdout_tail:
        message += f"\n[stdout]\n{stdout_tail}"
    if stderr_tail:
        message += f"\n[stderr]\n{stderr_tail}"

    metadata: dict[str, object] = {
        "returncode": proc.returncode,
        "command": command,
        "command_args": cmd_args,
        "execution_dir": str(cwd),
        "previous_dir": str(previous_cwd),
        "restored_dir": str(Path.cwd()),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
    }

    if proc.returncode != 0:
        metadata["error"] = "script_failed"

    return ToolResponse(
        content=[TextBlock(type="text", text=message)],
        metadata=metadata,
    )
