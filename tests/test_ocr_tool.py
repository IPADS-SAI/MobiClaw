import asyncio
import shutil
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mobiclaw.tools import extract_image_text_ocr


def _tesseract_languages() -> set[str]:
    try:
        import subprocess

        proc = subprocess.run(
            ["tesseract", "--list-langs"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = (proc.stdout or "").splitlines()
    except Exception:
        return set()
    langs: set[str] = set()
    for line in lines:
        text = line.strip()
        if not text or text.lower().startswith("list of available languages"):
            continue
        langs.add(text)
    return langs


def _response_text(resp) -> str:
    chunks = []
    for block in getattr(resp, "content", []) or []:
        if isinstance(block, dict):
            if block.get("type") == "text":
                chunks.append(str(block.get("text", "")))
            continue
        if getattr(block, "type", "") == "text":
            chunks.append(str(getattr(block, "text", "")))
    return "\n".join(chunks)


def test_extract_image_text_ocr_with_fixture():
    image_path = Path(__file__).parent / "fixtures" / "ocr_sample.png"
    assert image_path.exists(), f"fixture not found: {image_path}"

    if shutil.which("tesseract") is None:
        pytest.skip("tesseract is not installed in current environment")
    langs = _tesseract_languages()
    if "chi_sim" not in langs:
        pytest.skip("tesseract chi_sim language pack is missing (install tesseract-ocr-chi-sim)")

    resp = asyncio.run(
        extract_image_text_ocr(
            image_path=str(image_path),
            lang="chi_sim+eng",
            psm=6,
            oem=3,
            max_chars=4000,
        )
    )

    metadata = getattr(resp, "metadata", {}) or {}
    text = _response_text(resp)
    # print(text)

    assert metadata.get("engine") == "tesseract"
    assert metadata.get("path", "").endswith("ocr_sample.png")
    assert metadata.get("returncode") == 0
    assert "[OCR]" in text
    assert "ocr_sample.png" in text
