# -*- coding: utf-8 -*-
"""orchestrator 的私有工具函数模块。"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _extract_response_text(response: Any) -> str:
    """从 Agent 响应对象中提取可读文本。"""
    if response is None:
        return ""
    text = response.get_text_content() if hasattr(response, "get_text_content") else ""
    if text:
        return text
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        block_text = getattr(block, "text", "")
        if block_text:
            parts.append(block_text)
    return "\n".join(parts).strip()


def _collect_file_paths(text: str, output_path: str | None = None) -> list[Path]:
    """从模型回复与显式输出参数中收集文件路径。"""
    paths: list[Path] = []
    if output_path:
        paths.append(Path(output_path).expanduser())

    for raw in re.findall(r"\[(?:File|Download)\]\s+Wrote:\s*(.+)", text or ""):
        candidate = raw.strip()
        if candidate:
            paths.append(Path(candidate).expanduser())

    # Fallback: extract absolute local file paths that already exist on disk.
    # This helps capture files that the model reported as plain text paths.
    for raw in re.findall(r"(?<![A-Za-z0-9_])(/[^\s`\"'<>]+)", text or ""):
        candidate = raw.strip().rstrip(".,;:!?)")
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        try:
            resolved = path.resolve()
        except FileNotFoundError:
            resolved = path.absolute()
        if resolved.exists() and resolved.is_file():
            paths.append(path)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _ensure_output_file_written(output_path: str | None, reply: str) -> Path | None:
    """在输出文件缺失时将最终回复兜底写入到 output_path。"""
    target_raw = str(output_path or "").strip()
    if not target_raw:
        return None
    target = Path(target_raw).expanduser()
    if target.exists() and target.is_file():
        return target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(reply or "").strip(), encoding="utf-8")
        return target
    except OSError:
        logger.warning("Failed to persist final reply to output_path=%s", target_raw, exc_info=True)
        return None


def _build_file_entries(paths: list[Path]) -> list[dict[str, Any]]:
    """将文件路径转换为可序列化的文件信息。"""
    entries: list[dict[str, Any]] = []
    for path in paths:
        try:
            resolved = path.resolve()
        except FileNotFoundError:
            resolved = path.absolute()
        if not resolved.exists() or not resolved.is_file():
            continue
        stat = resolved.stat()
        mime_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        kind = "image" if mime_type.startswith("image/") else "document"
        entries.append(
            {
                "path": str(resolved),
                "name": resolved.name,
                "size": stat.st_size,
                "mime_type": mime_type,
                "kind": kind,
            }
        )
    return entries


def _merge_file_paths(existing: list[Path], incoming: list[Path]) -> list[Path]:
    """合并文件路径并保持顺序去重。"""
    merged: list[Path] = []
    seen: set[str] = set()
    for path in [*(existing or []), *(incoming or [])]:
        key = str(path)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(path)
    return merged


def _collect_tmp_dir_file_paths(temp_dir: str | None, *, max_files: int = 200) -> list[Path]:
    """从任务临时目录递归收集文件路径，作为工具输出兜底。"""
    if not temp_dir:
        return []

    root = Path(temp_dir).expanduser()
    if not root.exists() or not root.is_dir():
        return []

    files: list[Path] = []
    try:
        for item in root.rglob("*"):
            if item.is_file():
                files.append(item)
    except OSError:
        return []

    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if max_files > 0:
        files = files[:max_files]
    return files


def _trim_for_prompt(text: str, max_chars: int) -> str:
    """压缩空白并按长度截断，避免提示词过长。"""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if max_chars <= 0 or len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "..."


def _build_upstream_context(
    executions: list[dict[str, Any]],
    file_paths: list[Path],
    *,
    max_chars: int = 4000,
    max_steps: int = 20,
) -> str:
    """构建上游子任务摘要，供后续子任务引用。"""
    if not executions and not file_paths:
        return ""

    recent = executions[-max_steps:] if max_steps > 0 else executions
    sections: list[str] = ["上游子任务上下文（按时间顺序）:"]

    if recent:
        sections.append("前序子任务结果:")
        start_index = len(executions) - len(recent) + 1
        for offset, item in enumerate(recent):
            idx = start_index + offset
            agent = str(item.get("agent") or "unknown")
            subtask = _trim_for_prompt(str(item.get("task") or ""), 240)
            reply = _trim_for_prompt(str(item.get("reply") or ""), 600)
            if not reply:
                reply = "(无文本输出)"
            sections.append(f"- [{idx}] agent={agent}; task={subtask}; reply={reply}")

    if file_paths:
        sections.append("前序产出文件:")
        for path in file_paths:
            sections.append(f"- {path}")

    return _trim_for_prompt("\n".join(sections), max_chars)


def _build_external_context_text(external_context: dict[str, Any] | None) -> str:
    """将外部上下文渲染为可供 Agent 使用的提示片段。"""
    if not isinstance(external_context, dict) or not external_context:
        return ""

    feishu = external_context.get("feishu")
    if not isinstance(feishu, dict):
        return ""

    chat_id = str(feishu.get("chat_id") or "").strip()
    open_id = str(feishu.get("open_id") or "").strip()
    message_id = str(feishu.get("message_id") or "").strip()
    if not (chat_id or open_id or message_id):
        return ""

    return "\n".join(
        [
            "[Feishu Context]",
            "以下 ID 来自网关收到的真实飞书事件，调用飞书工具时请优先使用，不要猜测或改写：",
            f"- chat_id: {chat_id or '(empty)'}",
            f"- open_id: {open_id or '(empty)'}",
            f"- message_id: {message_id or '(empty)'}",
        ]
    )


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """从文本中解析首个可用 JSON 对象。"""
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    candidate = match.group(0).strip()
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    # Common LLM defect: extra closing brackets at tail, e.g. `...]]}`.
    repaired = candidate
    for _ in range(6):
        repaired_next = re.sub(r"\]\s*(\})\s*$", r"\1", repaired)
        if repaired_next == repaired:
            break
        repaired = repaired_next
        try:
            parsed = json.loads(repaired)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    # Generic tail-trim fallback for malformed trailing closers.
    trimmed = candidate
    for _ in range(8):
        if not trimmed or trimmed[-1] not in "]}":
            break
        trimmed = trimmed[:-1].rstrip()
        try:
            parsed = json.loads(trimmed)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def _project_root() -> Path:
    """返回项目根目录路径。"""
    return Path(__file__).resolve().parents[2]


def _create_job_output_paths(output_path: str | None) -> tuple[str, str, str]:
    """为本次任务创建 outputs/job_时间戳 目录并返回绝对路径。"""
    outputs_root = (_project_root() / "outputs").resolve()
    raw_output = (output_path or "").strip()
    reserved_output: Path | None = None
    if raw_output:
        candidate = Path(raw_output).expanduser()
        if candidate.is_absolute():
            try:
                reserved_output = candidate.resolve()
            except FileNotFoundError:
                reserved_output = candidate.absolute()
            parent = reserved_output.parent
            if parent.name.startswith("job_") and parent.parent == outputs_root:
                job_dir = parent
                tmp_dir = job_dir / "tmp"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                reserved_output.parent.mkdir(parents=True, exist_ok=True)
                return str(reserved_output), str(job_dir), str(tmp_dir)

    job_name = "job_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    job_dir = outputs_root / job_name
    tmp_dir = job_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if raw_output:
        candidate = Path(raw_output).expanduser()
        if candidate.is_absolute():
            candidate = Path(candidate.name)
        final_output = job_dir / candidate
    else:
        final_output = job_dir / "final_output.md"

    final_output.parent.mkdir(parents=True, exist_ok=True)
    try:
        resolved_output = final_output.resolve()
    except FileNotFoundError:
        resolved_output = final_output.absolute()

    return str(resolved_output), str(job_dir), str(tmp_dir)
