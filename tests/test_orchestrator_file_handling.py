from __future__ import annotations

from pathlib import Path

from seneschal import orchestrator


def test_collect_file_paths_extracts_existing_absolute_paths(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("content", encoding="utf-8")

    text = f"已生成文件：`{report}`"
    paths = orchestrator._collect_file_paths(text, output_path=None)
    resolved = {str(p.resolve()) for p in paths}
    assert str(report.resolve()) in resolved


def test_ensure_output_file_written_creates_missing_file(tmp_path: Path) -> None:
    target = tmp_path / "final_output.md"
    assert not target.exists()

    written = orchestrator._ensure_output_file_written(str(target), "final answer")
    assert written is not None
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "final answer"

