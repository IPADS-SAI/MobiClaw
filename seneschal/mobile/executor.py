from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from .config import resolve_device_config, resolve_provider_config
from .providers import get_provider_class
from .types import MobileExecutionResult


class _MockDevice:
    """Fallback device for dry-run/test envs without real phone connection."""

    def start_app(self, app):
        _ = app

    def app_start(self, package_name):
        _ = package_name

    def app_stop(self, package_name):
        _ = package_name

    def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"")

    def click(self, x, y):
        _ = (x, y)

    def long_click(self, x, y):
        _ = (x, y)

    def double_click(self, x, y):
        _ = (x, y)

    def input(self, text):
        _ = text

    def swipe(self, direction, scale=0.5):
        _ = (direction, scale)

    def swipe_with_coords(self, start_x, start_y, end_x, end_y):
        _ = (start_x, start_y, end_x, end_y)

    def keyevent(self, key):
        _ = key

    def dump_hierarchy(self):
        return "<hierarchy/>"


def _safe_task_name(task: str, max_len: int = 48) -> str:
    filtered = "".join(c if c.isalnum() or c in {"-", "_", " "} else "_" for c in task)
    compact = "_".join(filtered.split())
    return compact[:max_len] or "task"


def _collect_artifacts(run_dir: Path) -> dict[str, list[str]]:
    images: list[str] = []
    hierarchies: list[str] = []
    overlays: list[str] = []

    for p in sorted(run_dir.glob("*.jpg")):
        if p.name.endswith("_draw.jpg"):
            overlays.append(str(p))
        else:
            images.append(str(p))

    for p in sorted(run_dir.glob("*.xml")) + sorted(run_dir.glob("*.json")):
        if p.name in {"actions.json", "react.json", "execution_result.json"}:
            continue
        hierarchies.append(str(p))

    logs = [str(p) for p in sorted(run_dir.glob("*.log"))]
    return {
        "images": images,
        "hierarchies": hierarchies,
        "overlays": overlays,
        "logs": logs,
    }


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _append_unique_text(values: list[str], seen: set[str], candidate: str):
    text = str(candidate or "").strip()
    if not text:
        return
    if text in seen:
        return
    seen.add(text)
    values.append(text)


def _extract_texts_from_xml(path: Path) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return texts

    for node in root.iter():
        if not isinstance(node.attrib, dict):
            continue
        _append_unique_text(texts, seen, node.attrib.get("text"))
        _append_unique_text(texts, seen, node.attrib.get("content-desc"))
        _append_unique_text(texts, seen, node.attrib.get("hint"))
    return texts


def _extract_texts_from_json_value(value: Any, texts: list[str], seen: set[str]):
    if isinstance(value, dict):
        for k, v in value.items():
            key = str(k).strip().lower()
            if key in {
                "text",
                "content",
                "contentdesc",
                "content-desc",
                "contentdescription",
                "description",
                "hint",
                "label",
                "title",
                "name",
                "value",
            } and isinstance(v, (str, int, float)):
                _append_unique_text(texts, seen, str(v))
            else:
                _extract_texts_from_json_value(v, texts, seen)
    elif isinstance(value, list):
        for item in value:
            _extract_texts_from_json_value(item, texts, seen)


def _extract_texts_from_json(path: Path) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return texts
    _extract_texts_from_json_value(payload, texts, seen)
    return texts


def _extract_ocr_from_hierarchies(hierarchy_paths: list[str]) -> tuple[list[dict[str, Any]], str]:
    by_step: list[dict[str, Any]] = []
    full_lines: list[str] = []
    full_seen: set[str] = set()

    for index, hierarchy_path in enumerate(hierarchy_paths, start=1):
        path = Path(hierarchy_path)
        if not path.exists() or not path.is_file():
            continue

        step_texts: list[str] = []
        if path.suffix.lower() == ".xml":
            step_texts = _extract_texts_from_xml(path)
        elif path.suffix.lower() == ".json":
            step_texts = _extract_texts_from_json(path)

        if not step_texts:
            continue

        for text in step_texts:
            if text not in full_seen:
                full_seen.add(text)
                full_lines.append(text)

        by_step.append(
            {
                "step": index,
                "source_path": str(path),
                "text_lines": step_texts,
                "text": "\n".join(step_texts),
            }
        )

    return by_step, "\n".join(full_lines)


class MobileExecutor:
    def run(self, task: str, output_dir: str, provider: str | None = None) -> MobileExecutionResult:
        cfg = resolve_provider_config(provider=provider)
        provider_cls = get_provider_class(cfg.name)
        if provider_cls is None:
            raise ValueError(f"Unknown mobile provider: {cfg.name}")

        device_type, device_id = resolve_device_config()
        if device_type.lower() == "mock":
            device = _MockDevice()
        else:
            from .device import create_device
            device = create_device(device_type, adb_endpoint=device_id or None)

        base_dir = Path(output_dir)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = base_dir / cfg.name / f"{timestamp}_{_safe_task_name(task)}"
        run_dir.mkdir(parents=True, exist_ok=True)

        started = time.time()
        provider_kwargs: dict[str, Any] = {
            "task_description": task,
            "device": device,
            "data_dir": str(run_dir),
            "run_dir": str(run_dir),
            "device_type": device_type,
            "max_steps": cfg.max_steps,
            "draw": cfg.draw,
            "api_base": cfg.api_base,
            "api_key": cfg.api_key,
            "model": cfg.model,
            "temperature": cfg.temperature,
            **cfg.extras,
        }

        try:
            runner = provider_cls(**provider_kwargs)
            raw_result = runner.execute()
            status = str((raw_result or {}).get("status", "unknown"))
            success = status in {"success", "completed"}
            message = str((raw_result or {}).get("message", status))
        except Exception as exc:  # noqa: BLE001
            raw_result = {"status": "error", "error": str(exc), "message": str(exc)}
            success = False
            message = str(exc)

        elapsed = time.time() - started

        actions_json = _read_json(run_dir / "actions.json", {"actions": []})
        reacts_json = _read_json(run_dir / "react.json", [])
        actions = actions_json.get("actions", []) if isinstance(actions_json, dict) else []
        reasonings = [str(item.get("reasoning", "")) for item in reacts_json if isinstance(item, dict)]

        artifacts = _collect_artifacts(run_dir)
        final_screenshot = artifacts["images"][-1] if artifacts["images"] else ""
        ocr_by_step, ocr_full_text = _extract_ocr_from_hierarchies(artifacts.get("hierarchies", []))

        execution = {
            "schema_version": "seneschal_mobile_exec_v1",
            "run_dir": str(run_dir),
            "index_file": str(run_dir / "execution_result.json"),
            "summary": {
                "status_hint": str(raw_result.get("status", "unknown")),
                "step_count": int(raw_result.get("step_count", len(actions)) or 0),
                "action_count": int(len(actions)),
                "final_screenshot_path": final_screenshot,
                "elapsed_time": elapsed,
            },
            "artifacts": artifacts,
            "history": {
                "actions": actions,
                "reacts": reacts_json,
                "reasonings": reasonings,
            },
            "ocr": {
                "source": "hierarchy_xml_json",
                "by_step": ocr_by_step,
                "full_text": ocr_full_text,
            },
        }

        index_path = run_dir / "execution_result.json"
        index_path.write_text(json.dumps(execution, ensure_ascii=False, indent=2), encoding="utf-8")

        return MobileExecutionResult(success=success, message=message, execution=execution)
