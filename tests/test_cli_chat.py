"""Tests for mobiclaw.cli.chat."""
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from mobiclaw.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_chat_help(runner):
    """Chat command has --help and shows options."""
    r = runner.invoke(cli, ["chat", "--help"])
    assert r.exit_code == 0
    assert "context-id" in r.output or "context_id" in r.output
    assert "mode" in r.output
    assert "agent-hint" in r.output or "agent_hint" in r.output
    assert "skill-hint" in r.output or "skill_hint" in r.output
    assert "web-search" in r.output


def test_chat_repl_help_command(runner):
    """REPL /help command prints help text."""
    from mobiclaw.cli.chat import _run_repl

    calls = ["/help", "/quit"]

    async def fake_prompt(prompt_str):
        if not calls:
            raise EOFError()
        return calls.pop(0)

    with patch("mobiclaw.cli.chat.GatewayClient") as mock_cls:
        mock_client = mock_cls.return_value

        async def run_with_fake_prompt():
            with patch(
                "prompt_toolkit.PromptSession"
            ) as mock_session_cls:
                mock_session = mock_session_cls.return_value
                mock_session.prompt_async = AsyncMock(side_effect=fake_prompt)
                await _run_repl(
                    client=mock_client,
                    context_id=None,
                    mode="chat",
                    agent_hint=None,
                    skill_hint=None,
                    web_search_enabled=True,
                )

        import asyncio

        with patch("mobiclaw.cli.chat.print_text") as mock_print:
            asyncio.run(run_with_fake_prompt())
            # /help should have triggered print_text with help content
            printed = [str(c) for c in mock_print.call_args_list]
            assert any("Built-in commands" in str(p) for p in printed)
            assert any("/help" in str(p) for p in printed)
            assert any("/quit" in str(p) for p in printed)


def test_chat_repl_quit_exits(runner):
    """REPL /quit exits without calling submit_task."""
    from mobiclaw.cli.chat import _run_repl

    with patch("mobiclaw.cli.chat.GatewayClient") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.submit_task = AsyncMock()

        with patch(
            "prompt_toolkit.PromptSession"
        ) as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_session.prompt_async = AsyncMock(return_value="/quit")

            import asyncio

            asyncio.run(
                _run_repl(
                    client=mock_client,
                    context_id=None,
                    mode="chat",
                    agent_hint=None,
                    skill_hint=None,
                    web_search_enabled=True,
                )
            )
            mock_client.submit_task.assert_not_called()
