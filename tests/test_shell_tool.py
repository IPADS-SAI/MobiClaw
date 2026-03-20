import asyncio

from mobiclaw.tools.shell import run_shell_command


def test_run_shell_command_reports_unsafe_tokens() -> None:
    response = asyncio.run(run_shell_command("ls && pwd"))

    metadata = response.metadata or {}
    text = str(response.content[0].get("text") or "")

    assert metadata.get("error") == "unsafe_tokens"
    assert metadata.get("command") == "ls && pwd"
    assert {item["token"] for item in metadata.get("unsafe_tokens", [])} == {"&&"}
    assert "Found: &&" in text
    assert "Blocked operator tokens:" in text


def test_run_shell_command_reports_allowed_commands(monkeypatch) -> None:
    monkeypatch.setenv("MOBICLAW_SHELL_ALLOWLIST", "ls,pwd")

    response = asyncio.run(run_shell_command("git status"))

    metadata = response.metadata or {}
    text = str(response.content[0].get("text") or "")

    assert metadata.get("error") == "command_not_allowed"
    assert metadata.get("command") == "git status"
    assert metadata.get("requested_command") == "git"
    assert metadata.get("allowed_commands") == ["ls", "pwd"]
    assert "Command not allowed: git" in text
    assert "Allowed commands: ls, pwd" in text


def test_run_shell_command_success_returns_exit_code() -> None:
    response = asyncio.run(run_shell_command("pwd"))

    metadata = response.metadata or {}
    text = str(response.content[0].get("text") or "")

    assert metadata.get("returncode") == 0
    assert "[Shell] Exit code: 0" in text