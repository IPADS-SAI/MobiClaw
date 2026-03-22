"""Chat REPL command."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import click

from .config import resolve_config
from .http_client import GatewayClient
from .output import print_text

_CHAT_HELP = """
Built-in commands (prefix with /):
  /help      Show this help
  /attach <file>  Upload file for next message
  /mode <mode>    Set execution mode (chat, router, etc.)
  /context   Show current context_id
  /new       Start new session (clear context)
  /quit      Exit chat
"""


def _short_context(context_id: str | None) -> str:
    """First 6 chars of context_id or 'new' when none."""
    if not context_id:
        return "new"
    return (context_id[:6] if len(context_id) >= 6 else context_id) or "new"


def _ensure_chat_history_dir() -> str:
    """Ensure ~/.mobiclaw exists and return chat_history path."""
    base = Path.home() / ".mobiclaw"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "chat_history")


async def _run_repl(
    client: GatewayClient,
    context_id: str | None,
    mode: str,
    agent_hint: str | None,
    skill_hint: str | None,
    web_search_enabled: bool,
) -> None:
    """Run the chat REPL loop."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory

    history_path = _ensure_chat_history_dir()
    session = PromptSession(history=FileHistory(history_path))

    input_files: list[str] = []
    current_context = context_id
    current_mode = mode

    while True:
        prompt_str = f"[{_short_context(current_context)}] > "
        try:
            line = await session.prompt_async(prompt_str)
        except (EOFError, KeyboardInterrupt):
            break

        line = (line or "").strip()
        if not line:
            continue

        if line.startswith("/"):
            parts = line.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd == "/quit":
                break
            if cmd == "/help":
                print_text(_CHAT_HELP.strip())
                continue
            if cmd == "/context":
                print_text(current_context or "(none)")
                continue
            if cmd == "/new":
                current_context = None
                input_files = []
                print_text("New session. Context cleared.")
                continue
            if cmd == "/mode":
                if arg:
                    current_mode = arg
                    print_text(f"Mode set to: {current_mode}")
                else:
                    print_text(f"Current mode: {current_mode}")
                continue
            if cmd == "/attach":
                if not arg:
                    print_text("Usage: /attach <file>")
                    continue
                path = os.path.expanduser(arg.strip())
                if not Path(path).exists():
                    print_text(f"File not found: {path}")
                    continue
                try:
                    result = await client.upload_files([path])
                    paths = [
                        f.get("path", "")
                        for f in result.get("files", [])
                        if f.get("path")
                    ]
                    input_files.extend(paths)
                    print_text(f"Attached: {path}" + (f" -> {paths[-1]}" if paths else ""))
                except click.ClickException as e:
                    print_text(str(e))
                continue

            print_text(f"Unknown command: {cmd}. Type /help for help.")
            continue

        # Regular message: POST task
        payload: dict = {
            "task": line,
            "async_mode": False,
            "mode": current_mode,
            "agent_hint": agent_hint or None,
            "skill_hint": skill_hint or None,
            "context_id": current_context or None,
            "web_search_enabled": web_search_enabled,
            "input_files": input_files.copy(),
        }
        input_files = []  # Consume after use

        try:
            result = await client.submit_task(**payload)
        except click.ClickException as e:
            print_text(str(e))
            continue

        # Extract context_id from result for subsequent messages
        res = result.get("result") or {}
        for key in ("context_id", "session_id"):
            if res.get(key):
                current_context = str(res[key])
                break

        # Print reply and files
        reply = res.get("reply") or res.get("text") or res.get("text_content") or ""
        if reply:
            print_text(str(reply).strip())
        files = res.get("files") or []
        if files:
            print_text("\nFiles:")
            for f in files:
                name = f.get("name") or f.get("path") or "?"
                path = f.get("path", "")
                url = f.get("download_url", "")
                if url:
                    print_text(f"  - {name}: {url}")
                elif path:
                    print_text(f"  - {name}: {path}")
                else:
                    print_text(f"  - {name}")
        if not reply and not files and result.get("error"):
            print_text(f"Error: {result['error']}")


def register_chat_command(cli_group: click.Group) -> None:
    """Register chat REPL subcommand."""

    @cli_group.command("chat")
    @click.option("--context-id", "context_id", default=None, help="Session/context ID")
    @click.option("--mode", default="chat", help="Execution mode (chat, router, etc.)")
    @click.option("--agent-hint", "agent_hint", default=None, help="Agent selection hint")
    @click.option("--skill-hint", "skill_hint", default=None, help="Skill selection hint")
    @click.option(
        "--web-search/--no-web-search",
        "web_search_enabled",
        default=True,
        help="Enable/disable web search",
    )
    @click.pass_context
    def chat(
        ctx,
        context_id: str | None,
        mode: str,
        agent_hint: str | None,
        skill_hint: str | None,
        web_search_enabled: bool,
    ):
        """Interactive chat REPL. Type /help for built-in commands."""
        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        asyncio.run(
            _run_repl(
                client=client,
                context_id=context_id,
                mode=mode,
                agent_hint=agent_hint,
                skill_hint=skill_hint,
                web_search_enabled=web_search_enabled,
            )
        )
