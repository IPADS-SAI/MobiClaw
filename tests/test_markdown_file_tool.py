from __future__ import annotations

from pathlib import Path

from mobiclaw.tools.file import read_markdown_file


def test_read_markdown_file_success(tmp_path: Path) -> None:
    file_path = tmp_path / "notes.md"
    file_path.write_text("# Title\n\nhello", encoding="utf-8")

    response = read_markdown_file(str(file_path))

    metadata = response.metadata or {}
    text = str(response.content[0].get("text") or "")
    assert "# Title" in text
    assert metadata.get("path") == str(file_path.resolve())
    assert metadata.get("truncated") is False


def test_read_markdown_file_rejects_non_md(tmp_path: Path) -> None:
    file_path = tmp_path / "notes.txt"
    file_path.write_text("hello", encoding="utf-8")

    response = read_markdown_file(str(file_path))

    metadata = response.metadata or {}
    assert metadata.get("error") == "invalid_extension"


def test_read_markdown_file_respects_root(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    monkeypatch.setenv("MOBICLAW_FILE_READ_ROOT", str(root))

    response = read_markdown_file(str(outside))

    metadata = response.metadata or {}
    assert metadata.get("error") == "path_outside_root"


def test_read_markdown_file_truncates_content(tmp_path: Path) -> None:
    file_path = tmp_path / "long.md"
    file_path.write_text("abcdef", encoding="utf-8")

    response = read_markdown_file(str(file_path), max_chars=3)

    metadata = response.metadata or {}
    text = str(response.content[0].get("text") or "")
    assert text == "abc"
    assert metadata.get("truncated") is True
    assert metadata.get("returned_chars") == 3
    assert metadata.get("total_chars") == 6
