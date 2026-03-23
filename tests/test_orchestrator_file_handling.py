from __future__ import annotations

from pathlib import Path

from mobiclaw import orchestrator


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


def test_collect_tmp_dir_file_paths_only_keeps_document_and_image_files(tmp_path: Path) -> None:
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    keep_md = tmp_dir / "report.md"
    keep_md.write_text("summary", encoding="utf-8")
    keep_pdf = tmp_dir / "slides.pdf"
    keep_pdf.write_bytes(b"%PDF")
    keep_docx = tmp_dir / "notes.docx"
    keep_docx.write_bytes(b"PK")
    keep_pptx = tmp_dir / "deck.pptx"
    keep_pptx.write_bytes(b"PK")
    keep_xlsx = tmp_dir / "table.xlsx"
    keep_xlsx.write_bytes(b"PK")
    keep_png = tmp_dir / "chart.png"
    keep_png.write_bytes(b"\x89PNG\r\n\x1a\n")

    drop_js = tmp_dir / "script.js"
    drop_js.write_text("console.log('x')", encoding="utf-8")
    drop_zip = tmp_dir / "bundle.zip"
    drop_zip.write_bytes(b"PK")
    drop_bin = tmp_dir / "artifact.bin"
    drop_bin.write_bytes(b"\x00\x01")

    collected = orchestrator._collect_tmp_dir_file_paths(str(tmp_dir), max_files=200)
    names = {p.name for p in collected}

    assert "report.md" in names
    assert "slides.pdf" in names
    assert "notes.docx" in names
    assert "deck.pptx" in names
    assert "table.xlsx" in names
    assert "chart.png" in names

    assert "script.js" not in names
    assert "bundle.zip" not in names
    assert "artifact.bin" not in names

