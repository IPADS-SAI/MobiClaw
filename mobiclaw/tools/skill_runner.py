# -*- coding: utf-8 -*-
"""Run skill commands in a specified execution directory."""

from __future__ import annotations

import os
import logging
import re
import shlex
import subprocess
from pathlib import Path

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse


logger = logging.getLogger(__name__)

_SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills"
_RUNTIME_NAMES = {"python", "python3", "bash", "sh", "zsh", "node", "npm", "npx", "uv", "pip", "pip3"}
_MAX_ALLOWED_COMMANDS_IN_TEXT = 20
_MAX_CHAIN_SEGMENTS = 2
_GLOBAL_HARMLESS_COMMANDS = ["cat", "ls", "pwd", "echo", "head", "tail", "wc"]
_ANSI_BOLD = "\033[1m"
_ANSI_YELLOW = "\033[93m"
_ANSI_RESET = "\033[0m"
_CHAIN_DISALLOWED_TOKENS = ("||", "|", ";")
_FENCE_LANG_TO_RUNTIMES = {
    "python": ["python", "python3"],
    "py": ["python", "python3"],
    "bash": ["bash", "sh", "zsh"],
    "shell": ["bash", "sh", "zsh"],
    "sh": ["sh", "bash", "zsh"],
    "zsh": ["zsh", "bash", "sh"],
    "javascript": ["node", "npm", "npx"],
    "js": ["node", "npm", "npx"],
    "typescript": ["node", "npm", "npx"],
    "ts": ["node", "npm", "npx"],
    "node": ["node", "npm", "npx"],
    "npm": ["npm", "npx", "node"],
}


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


def _runtimes_from_fence_lang(lang: str) -> list[str]:
    """Infer runtime command names from fenced code block language tags."""
    normalized = (lang or "").strip().lower()
    if not normalized:
        return []

    runtimes = list(_FENCE_LANG_TO_RUNTIMES.get(normalized, []))
    if not runtimes:
        base = normalized.split(" ", 1)[0]
        runtimes = list(_FENCE_LANG_TO_RUNTIMES.get(base, []))
    # Treat explicit runtime names as directly executable candidates.
    if not runtimes and normalized in _RUNTIME_NAMES:
        runtimes = [normalized]

    deduped: list[str] = []
    seen: set[str] = set()
    for runtime in runtimes:
        if runtime not in seen:
            deduped.append(runtime)
            seen.add(runtime)
    return deduped


def _color_warning(message: str) -> str:
    return f"{_ANSI_BOLD}{_ANSI_YELLOW}{message}{_ANSI_RESET}"


def _extract_commands_from_skill_md(skill_md_path: Path) -> list[str]:
    try:
        content = skill_md_path.read_text(encoding="utf-8")
    except OSError:
        return []

    candidates: list[str] = []
    fence_runtimes: list[str] = []

    # Extract command-like lines from fenced code blocks.
    fence_pattern = re.compile(r"```(?P<lang>[A-Za-z0-9_+-]*)\n(?P<body>.*?)```", flags=re.DOTALL | re.IGNORECASE)
    for match in fence_pattern.finditer(content):
        lang = (match.group("lang") or "").strip().lower()
        block = match.group("body")
        fence_runtimes.extend(_runtimes_from_fence_lang(lang))
        for line in block.splitlines():
            line = line.strip()
            if _looks_like_command_line(line):
                candidates.append(line)

    # If a fenced block indicates executable language, allow corresponding runtime wrappers.
    candidates.extend(fence_runtimes)

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


def _skill_markdown_files(skill_dir: Path) -> list[Path]:
    """Return markdown files under one skill directory with SKILL.md first."""
    if not skill_dir.exists() or not skill_dir.is_dir():
        return []

    files = [p for p in skill_dir.iterdir() if p.is_file() and p.suffix.lower() == ".md"]
    files.sort(key=lambda p: (0 if p.name.lower() == "skill.md" else 1, p.name.lower()))
    return files


def _extract_commands_from_skill_dir(skill_dir: Path) -> tuple[list[str], list[str]]:
    """Extract allowed commands from all markdown files in a skill directory."""
    commands: list[str] = []
    docs: list[str] = []
    seen_cmds: set[str] = set()

    for md_path in _skill_markdown_files(skill_dir):
        extracted = _extract_commands_from_skill_md(md_path)
        if extracted:
            docs.append(str(md_path))
        for cmd in extracted:
            if cmd not in seen_cmds:
                commands.append(cmd)
                seen_cmds.add(cmd)

    return commands, docs


def _format_allowed_commands_for_text(allowed_commands: list[str], limit: int = _MAX_ALLOWED_COMMANDS_IN_TEXT) -> str:
    if not allowed_commands:
        return "<none>"

    visible = allowed_commands[:limit]
    text = "; ".join(visible)
    if len(allowed_commands) > limit:
        text += f"; ... ({len(allowed_commands) - limit} more)"
    return text


def _merge_allowed_commands(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item in seen:
                continue
            merged.append(item)
            seen.add(item)
    return merged


def _split_command_chain(command: str, *, max_segments: int = _MAX_CHAIN_SEGMENTS) -> tuple[list[str] | None, str | None]:
    raw = (command or "").strip()
    if not raw:
        return None, "empty_command"

    if _find_unsupported_operator_token(raw):
        return None, "unsupported_operator"

    segments = [part.strip() for part in raw.split("&&")]
    if any(not segment for segment in segments):
        return None, "invalid_chain_syntax"
    if len(segments) > max_segments:
        return None, "invalid_chain_length"
    return segments, None


def _find_unsupported_operator_token(command: str) -> str | None:
    raw = (command or "").strip()
    if not raw:
        return None

    for token in _CHAIN_DISALLOWED_TOKENS:
        if token in raw:
            return token
    return None


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
        # Runtime-only whitelist entries (e.g. "node") permit runtime+script variants.
        if len(allowed_sig) == 1 and command_sig[0] == allowed_sig[0]:
            logger.warning(
                _color_warning(
                    "skill_runner.runtime_only_whitelist_match "
                    f"runtime={command_sig[0]} command={command} "
                    f"allowed_runtime_entry={allowed}"
                )
            )
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
        command: Executable command string. Don't include "cd" operators here; just the command to run. Other operators like "|", ";", "||" are also not allowed for safety reasons.
        execution_dir: Directory to run command in.
        timeout_s: Optional timeout in seconds.
    """
    command = (command or "").strip()
    if not command:
        return ToolResponse(
            content=[TextBlock(type="text", text="[SkillRunner] Empty command")],
            metadata={"error": "empty_command"},
        )

    command_segments, chain_error = _split_command_chain(command)
    if chain_error or not command_segments:
        unsupported_token = _find_unsupported_operator_token(command) if chain_error == "unsupported_operator" else None
        detail = ""
        if unsupported_token:
            detail = (
                f" (unsupported token: {unsupported_token}; only '&&' is allowed, max {_MAX_CHAIN_SEGMENTS} segments)"
            )
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[SkillRunner] Invalid command chain: {chain_error}{detail}")],
            metadata={
                "error": chain_error or "invalid_command_chain",
                "command": command,
                "max_chain_segments": _MAX_CHAIN_SEGMENTS,
                "disallowed_operators": list(_CHAIN_DISALLOWED_TOKENS),
                "unsupported_operator_token": unsupported_token or "",
            },
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
    allowed_commands, allowed_docs = _extract_commands_from_skill_dir(cwd)
    effective_allowed_commands = _merge_allowed_commands(allowed_commands, _GLOBAL_HARMLESS_COMMANDS)
    if not allowed_commands:
        all_segments_harmless = all(_is_command_allowed(segment, _GLOBAL_HARMLESS_COMMANDS) for segment in command_segments)
        if all_segments_harmless:
            logger.info(
                "skill_runner.global_whitelist_match command=%s execution_dir=%s",
                command,
                cwd,
            )
        else:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"[SkillRunner] No command whitelist found in: {skill_md_path}. "
                            "No command-like entries could be extracted from SKILL.md or sibling .md files, "
                            "so there are currently no allowed commands for this skill."
                        ),
                    ),
                ],
                metadata={
                    "error": "skill_whitelist_not_found",
                    "execution_dir": str(cwd),
                    "skill_md": str(skill_md_path),
                    "skill_docs": allowed_docs,
                    "allowed_commands": [],
                    "global_harmless_commands": _GLOBAL_HARMLESS_COMMANDS,
                    "segment_count": len(command_segments),
                },
            )

    timeout_value = timeout_s
    if timeout_value is None:
        timeout_value = float(os.environ.get("MOBICLAW_SKILL_SCRIPT_TIMEOUT_S", "120"))

    parsed_segments: list[list[str]] = []
    for idx, segment in enumerate(command_segments, start=1):
        try:
            cmd_args = shlex.split(segment)
        except ValueError as exc:
            return ToolResponse(
                content=[TextBlock(type="text", text=f"[SkillRunner] Command parse error in segment {idx}: {exc}")],
                metadata={
                    "error": "command_parse_error",
                    "command": command,
                    "segment_index": idx,
                    "segment_command": segment,
                },
            )

        if not cmd_args:
            return ToolResponse(
                content=[TextBlock(type="text", text=f"[SkillRunner] Empty command tokens in segment {idx}")],
                metadata={
                    "error": "empty_command_tokens",
                    "command": command,
                    "segment_index": idx,
                    "segment_command": segment,
                },
            )

        if not _is_command_allowed(segment, effective_allowed_commands):
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"[SkillRunner] Segment {idx} is not allowed by SKILL.md whitelist: {segment}. "
                            f"Allowed commands from SKILL.md + global harmless set: {_format_allowed_commands_for_text(effective_allowed_commands)} "
                            "(includes sibling .md files in this skill directory)."
                        ),
                    )
                ],
                metadata={
                    "error": "script_not_allowed",
                    "command": command,
                    "segment_index": idx,
                    "segment_command": segment,
                    "execution_dir": str(cwd),
                    "skill_md": str(skill_md_path),
                    "skill_docs": allowed_docs,
                    "allowed_commands": allowed_commands,
                    "global_harmless_commands": _GLOBAL_HARMLESS_COMMANDS,
                    "allowed_command_hints": effective_allowed_commands[:_MAX_ALLOWED_COMMANDS_IN_TEXT],
                },
            )
        parsed_segments.append(cmd_args)

    previous_cwd = Path.cwd()
    os.chdir(cwd)

    segment_results: list[dict[str, object]] = []
    overall_returncode = 0
    try:
        for idx, cmd_args in enumerate(parsed_segments, start=1):
            segment_command = command_segments[idx - 1]
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
                    metadata={
                        "error": "runtime_not_found",
                        "runtime": cmd_args[0],
                        "command": command,
                        "segment_index": idx,
                        "segment_command": segment_command,
                    },
                )
            except subprocess.TimeoutExpired:
                return ToolResponse(
                    content=[TextBlock(type="text", text=f"[SkillRunner] Script timeout after {timeout_value:.1f}s")],
                    metadata={
                        "error": "timeout",
                        "timeout_s": timeout_value,
                        "command": command,
                        "segment_index": idx,
                        "segment_command": segment_command,
                        "execution_dir": str(cwd),
                    },
                )

            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()
            stdout_tail = stdout[-4000:]
            stderr_tail = stderr[-2000:]
            segment_results.append(
                {
                    "index": idx,
                    "command": segment_command,
                    "command_args": cmd_args,
                    "returncode": int(proc.returncode),
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                }
            )
            overall_returncode = int(proc.returncode)
            if overall_returncode != 0:
                break
    finally:
        os.chdir(previous_cwd)

    message = f"[SkillRunner] Exit code: {overall_returncode}"
    message += f"\n[execution_dir] {cwd}"
    message += f"\n[command] {command}"
    for item in segment_results:
        idx = int(item.get("index") or 0)
        message += f"\n[segment:{idx}] {str(item.get('command') or '')}"
        message += f"\n[segment:{idx}:exit_code] {int(item.get('returncode') or 0)}"
        stdout_tail = str(item.get("stdout_tail") or "")
        stderr_tail = str(item.get("stderr_tail") or "")
        if stdout_tail:
            message += f"\n[segment:{idx}:stdout]\n{stdout_tail}"
        if stderr_tail:
            message += f"\n[segment:{idx}:stderr]\n{stderr_tail}"

    last_segment = segment_results[-1] if segment_results else {}
    metadata: dict[str, object] = {
        "returncode": overall_returncode,
        "command": command,
        "command_args": parsed_segments[0] if parsed_segments else [],
        "execution_dir": str(cwd),
        "previous_dir": str(previous_cwd),
        "restored_dir": str(Path.cwd()),
        "stdout_tail": str(last_segment.get("stdout_tail") or ""),
        "stderr_tail": str(last_segment.get("stderr_tail") or ""),
        "segment_count": len(command_segments),
        "segments": segment_results,
    }

    if overall_returncode != 0:
        metadata["error"] = "script_failed"

    return ToolResponse(
        content=[TextBlock(type="text", text=message)],
        metadata=metadata,
    )
