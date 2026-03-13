# -*- coding: utf-8 -*-
"""Image OCR tool powered by local tesseract command."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

logger = logging.getLogger(__name__)


def _trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


async def extract_image_text_ocr(
    image_path: str,
    lang: str = "chi_sim+eng",
    psm: int = 6,
    oem: int = 3,
    max_chars: int | None = None,
) -> ToolResponse:
    """Extract text from an image file with tesseract OCR.

    Args:
        image_path: Local image file path.
        lang: Tesseract language pack string.
        psm: Tesseract page segmentation mode.
        oem: Tesseract OCR engine mode.
        max_chars: Optional maximum number of returned characters.
    """
    resolved_path = (image_path or "").strip()
    logger.info("ocr.extract path=%s lang=%s", resolved_path, lang)
    if not resolved_path:
        return ToolResponse(
            content=[TextBlock(type="text", text="[OCR] Empty image path.")],
            metadata={"error": "empty_path"},
        )

    target = Path(resolved_path).expanduser()
    if not target.exists():
        return ToolResponse(
            content=[TextBlock(type="text", text="[OCR] File not found.")],
            metadata={"error": "not_found", "path": str(target)},
        )

    if not target.is_file():
        return ToolResponse(
            content=[TextBlock(type="text", text="[OCR] Path is not a file.")],
            metadata={"error": "not_a_file", "path": str(target)},
        )

    if shutil.which("tesseract") is None:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="[OCR] tesseract is not installed or not in PATH.",
                )
            ],
            metadata={"error": "tesseract_not_found"},
        )

    normalized_lang = (lang or "chi_sim+eng").strip() or "chi_sim+eng"
    normalized_psm = max(0, min(int(psm), 13))
    normalized_oem = max(0, min(int(oem), 3))

    timeout_s = float(os.environ.get("SENESCHAL_OCR_TIMEOUT", "60"))
    max_chars_env = int(os.environ.get("SENESCHAL_OCR_MAX_CHARS", "12000"))
    max_chars_value = int(max_chars) if max_chars is not None else max_chars_env
    max_chars_value = max(1000, min(max_chars_value, 200000))

    cmd = [
        "tesseract",
        str(target),
        "stdout",
        "-l",
        normalized_lang,
        "--psm",
        str(normalized_psm),
        "--oem",
        str(normalized_oem),
    ]

    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return ToolResponse(
            content=[TextBlock(type="text", text="[OCR] Command timed out.")],
            metadata={"error": "timeout"},
        )
    except Exception as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[OCR] Failed to run tesseract: {exc}")],
            metadata={"error": str(exc)},
        )

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stderr = _trim_text(stderr, 2000)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"[OCR] tesseract failed (exit={proc.returncode}).\n{stderr}",
                )
            ],
            metadata={
                "error": "ocr_failed",
                "returncode": proc.returncode,
                "stderr": stderr,
            },
        )

    text = (proc.stdout or "").strip()
    text = _trim_text(text, max_chars_value)
    if not text:
        text = "[OCR] No text recognized."

    return ToolResponse(
        content=[TextBlock(type="text", text=f"[OCR] {target}\n{text}")],
        metadata={
            "path": str(target),
            "engine": "tesseract",
            "lang": normalized_lang,
            "psm": normalized_psm,
            "oem": normalized_oem,
            "returncode": proc.returncode,
        },
    )
