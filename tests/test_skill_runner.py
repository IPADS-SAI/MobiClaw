import asyncio
import subprocess
from pathlib import Path

from mobiclaw import orchestrator
from mobiclaw.tools.skill_runner import run_skill_script


def test_run_skill_script_runs_command_in_execution_dir(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=str(Path.cwd()),
            stderr="",
        )

    monkeypatch.setattr("mobiclaw.tools.skill_runner.subprocess.run", fake_run)

    skill_root = Path(__file__).resolve().parents[1] / "mobiclaw" / "skills" / "pptx"
    response = asyncio.run(
        run_skill_script(
            command="python -m markitdown presentation.pptx",
            execution_dir=str(skill_root),
            timeout_s=15,
        )
    )

    metadata = response.metadata or {}
    assert metadata.get("returncode") == 0
    execution_dir = Path(str(metadata.get("execution_dir", ""))).resolve()
    assert execution_dir == skill_root.resolve()
    assert str(skill_root.resolve()) in str(metadata.get("stdout_tail", ""))


def test_run_skill_script_rejects_execution_dir_outside_skill_root(tmp_path: Path) -> None:
    response = asyncio.run(
        run_skill_script(
            command="python -m markitdown presentation.pptx",
            execution_dir=str(tmp_path),
            timeout_s=15,
        )
    )

    metadata = response.metadata or {}
    assert metadata.get("error") == "execution_dir_not_in_skill_root"


def test_run_skill_script_invalid_execution_dir() -> None:
    missing_dir = "/tmp/seneschal_missing_execution_dir_12345"
    response = asyncio.run(
        run_skill_script(
            command="/bin/pwd",
            execution_dir=missing_dir,
            timeout_s=15,
        )
    )

    metadata = response.metadata or {}
    assert metadata.get("error") == "execution_dir_not_found"


def test_skill_prompt_context_includes_execution_dir() -> None:
    context = orchestrator._skill_prompt_context(["pptx"])
    assert "[Skill: pptx]" in context
    assert "execution_dir (just for skill scripts):" in context


def test_run_skill_script_restores_previous_dir(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="ok",
            stderr="",
        )

    monkeypatch.setattr("mobiclaw.tools.skill_runner.subprocess.run", fake_run)

    before = Path.cwd().resolve()
    skill_root = Path(__file__).resolve().parents[1] / "mobiclaw" / "skills" / "pptx"
    response = asyncio.run(
        run_skill_script(
            command="python -m markitdown presentation.pptx",
            execution_dir=str(skill_root),
            timeout_s=15,
        )
    )
    metadata = response.metadata or {}
    assert Path(str(metadata.get("previous_dir", ""))).resolve() == before
    assert Path(str(metadata.get("restored_dir", ""))).resolve() == before
    assert Path.cwd().resolve() == before


def test_run_skill_script_rejects_non_whitelisted_command() -> None:
    skill_root = Path(__file__).resolve().parents[1] / "mobiclaw" / "skills" / "pptx"
    response = asyncio.run(
        run_skill_script(
            command="/bin/pwd",
            execution_dir=str(skill_root),
            timeout_s=15,
        )
    )

    metadata = response.metadata or {}
    assert metadata.get("error") == "script_not_allowed"
    assert metadata.get("skill_md")
