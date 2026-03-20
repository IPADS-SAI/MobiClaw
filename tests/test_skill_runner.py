import asyncio
import subprocess
from pathlib import Path

from mobiclaw import orchestrator
from mobiclaw.orchestrator.types import SkillProfile
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


def test_run_skill_script_reports_missing_whitelist(monkeypatch, tmp_path: Path) -> None:
    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "empty-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Empty skill\n\nThis file has no runnable command examples.\n", encoding="utf-8")

    monkeypatch.setattr("mobiclaw.tools.skill_runner._SKILL_ROOT", skill_root)

    response = asyncio.run(
        run_skill_script(
            command="python -m anything",
            execution_dir=str(skill_dir),
            timeout_s=15,
        )
    )

    metadata = response.metadata or {}
    text = str(response.content[0].get("text") or "")
    assert metadata.get("error") == "skill_whitelist_not_found"
    assert metadata.get("allowed_commands") == []
    assert "there are currently no allowed commands for this skill" in text


def test_run_skill_script_allows_commands_from_sibling_markdown(monkeypatch, tmp_path: Path) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="ok",
            stderr="",
        )

    monkeypatch.setattr("mobiclaw.tools.skill_runner.subprocess.run", fake_run)

    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Demo\n\nNo commands here.\n", encoding="utf-8")
    (skill_dir / "guide.md").write_text("```bash\npython scripts/demo.py input.txt\n```\n", encoding="utf-8")

    monkeypatch.setattr("mobiclaw.tools.skill_runner._SKILL_ROOT", skill_root)

    response = asyncio.run(
        run_skill_script(
            command="python scripts/demo.py input.txt",
            execution_dir=str(skill_dir),
            timeout_s=15,
        )
    )

    metadata = response.metadata or {}
    assert metadata.get("returncode") == 0


def test_run_skill_script_allows_node_from_javascript_fence(monkeypatch, tmp_path: Path) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="ok",
            stderr="",
        )

    monkeypatch.setattr("mobiclaw.tools.skill_runner.subprocess.run", fake_run)

    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "js-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "```javascript\nconst x = 1;\nslide.addText([\n```\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("mobiclaw.tools.skill_runner._SKILL_ROOT", skill_root)

    response = asyncio.run(
        run_skill_script(
            command="node scripts/build.js",
            execution_dir=str(skill_dir),
            timeout_s=15,
        )
    )

    metadata = response.metadata or {}
    assert metadata.get("returncode") == 0


def test_skill_prompt_context_includes_execution_dir() -> None:
    context = orchestrator._skill_prompt_context(["pptx"])
    assert "[Skill: pptx]" in context
    assert "execution_dir (just for skill scripts):" in context


def test_skill_prompt_context_includes_markdown_filename_pairs(monkeypatch, tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Base skill\n\nrun this", encoding="utf-8")
    (skill_dir / "guide.md").write_text("# Extra guide\n\nmore details", encoding="utf-8")

    profile = SkillProfile(
        name="demo",
        description="demo",
        content_hint="demo",
        full_content="# Base skill\n\nrun this",
        skill_dir=str(skill_dir),
    )
    monkeypatch.setattr("mobiclaw.orchestrator.skills._available_skill_profiles", lambda: (profile,))

    context = orchestrator._skill_prompt_context(["demo"])

    assert "[Skill File: SKILL.md]" in context
    assert "[Skill File: guide.md]" in context
    assert "# Base skill" in context
    assert "# Extra guide" in context


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
    text = str(response.content[0].get("text") or "")
    assert metadata.get("error") == "script_not_allowed"
    assert metadata.get("skill_md")
    assert metadata.get("allowed_commands")
    assert metadata.get("allowed_command_hints")
    assert "Allowed commands from SKILL.md:" in text
