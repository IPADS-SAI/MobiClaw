import asyncio
import shlex
import subprocess
from pathlib import Path

from mobiclaw.tools.shell import run_shell_command


def test_run_shell_command_reports_unsafe_tokens() -> None:
    response = asyncio.run(run_shell_command("ls | pwd"))

    metadata = response.metadata or {}
    text = str(response.content[0].get("text") or "")

    assert metadata.get("error") == "unsafe_tokens"
    assert metadata.get("command") == "ls | pwd"
    assert {item["token"] for item in metadata.get("unsafe_tokens", [])} == {"|"}
    assert "Found: |" in text
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
    assert "Command not allowed in segment 1: git" in text
    assert "Allowed commands: ls, pwd" in text


def test_run_shell_command_success_returns_exit_code() -> None:
    response = asyncio.run(run_shell_command("pwd"))

    metadata = response.metadata or {}
    text = str(response.content[0].get("text") or "")

    assert metadata.get("returncode") == 0
    assert "[Shell] Exit code: 0" in text


def test_run_shell_command_supports_two_segment_chain() -> None:
    response = asyncio.run(run_shell_command("pwd && ls"))

    metadata = response.metadata or {}
    text = str(response.content[0].get("text") or "")

    assert metadata.get("returncode") == 0
    assert metadata.get("segment_count") == 2
    assert len(metadata.get("segments", [])) == 2
    assert "[segment:1] pwd" in text
    assert "[segment:2] ls" in text


def test_run_shell_command_supports_builtin_cd() -> None:
    response = asyncio.run(run_shell_command("cd"))

    metadata = response.metadata or {}
    text = str(response.content[0].get("text") or "")

    assert metadata.get("returncode") == 0
    segments = metadata.get("segments", [])
    assert len(segments) == 1
    assert segments[0].get("builtin") == "cd"
    assert "[segment:1] cd" in text


def test_run_shell_command_cd_chain_updates_cwd(tmp_path: Path) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    expected_dir = str(workdir.resolve())

    response = asyncio.run(run_shell_command(f"cd {shlex.quote(expected_dir)} && pwd"))

    metadata = response.metadata or {}
    text = str(response.content[0].get("text") or "")

    assert metadata.get("returncode") == 0
    assert expected_dir in text


def test_run_shell_command_cd_rejects_missing_directory() -> None:
    response = asyncio.run(run_shell_command("cd /path/that/does/not/exist"))

    metadata = response.metadata or {}
    text = str(response.content[0].get("text") or "")

    assert metadata.get("error") == "cd_directory_not_found"
    assert "cd failed" in text


def test_run_shell_command_rejects_chain_longer_than_two_segments() -> None:
    response = asyncio.run(run_shell_command("pwd && ls && whoami"))

    metadata = response.metadata or {}
    assert metadata.get("error") == "invalid_chain_length"


def test_run_shell_command_rejects_disallowed_second_segment(monkeypatch) -> None:
    monkeypatch.setenv("MOBICLAW_SHELL_ALLOWLIST", "pwd,ls")

    response = asyncio.run(run_shell_command("pwd && git status"))
    metadata = response.metadata or {}

    assert metadata.get("error") == "command_not_allowed"
    assert metadata.get("segment_index") == 2
    assert metadata.get("requested_command") == "git"


def test_run_shell_command_short_circuits_when_first_segment_fails(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(*args, **kwargs):
        calls.append(list(args[0]))
        if len(calls) == 1:
            return subprocess.CompletedProcess(args=args[0], returncode=2, stdout="", stderr="failed")
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("mobiclaw.tools.shell.subprocess.run", fake_run)

    response = asyncio.run(run_shell_command("pwd && ls"))
    metadata = response.metadata or {}

    assert metadata.get("returncode") == 2
    assert metadata.get("error") == "shell_command_failed"
    assert len(calls) == 1