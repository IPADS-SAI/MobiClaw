# -*- coding: utf-8 -*-
"""MobiAgent HTTP gateway server.

Provides a stable API for Seneschal:
- POST /api/v1/collect
- POST /api/v1/action
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import os
import httpx
import json
import time
import uuid
import sys
from pathlib import Path
import subprocess
import base64
import re
import xml.etree.ElementTree as ET
from fastapi import FastAPI, HTTPException, Header, status
from pydantic import BaseModel, Field

from seneschal.tools.mock_data import get_mock_collect_result, get_mock_action_result


@dataclass
class GatewayConfig:
    mode: str
    api_key: str
    collect_url: str | None
    action_url: str | None
    v1_url: str | None
    queue_dir: Path
    result_dir: Path
    cli_cmd_template: str | None
    cli_workdir: Path | None
    cli_task_dir: Path
    cli_data_dir: Path
    vl_base_url: str | None
    vl_api_key: str | None
    vl_model: str | None
    timeout_s: float


def load_config() -> GatewayConfig:
    return GatewayConfig(
        mode=os.environ.get("MOBIAGENT_SERVER_MODE", "cli"),
        api_key=os.environ.get("MOBI_AGENT_API_KEY", ""),
        collect_url=os.environ.get("MOBIAGENT_COLLECT_URL"),
        action_url=os.environ.get("MOBIAGENT_ACTION_URL"),
        v1_url=os.environ.get("MOBIAGENT_V1_URL"),
        queue_dir=Path(os.environ.get("MOBIAGENT_QUEUE_DIR", "mobiagent_server/queue")),
        result_dir=Path(os.environ.get("MOBIAGENT_RESULT_DIR", "mobiagent_server/results")),
        cli_cmd_template=os.environ.get("MOBIAGENT_CLI_CMD"),
        cli_workdir=Path(os.environ["MOBIAGENT_CLI_WORKDIR"]).expanduser() if os.environ.get("MOBIAGENT_CLI_WORKDIR") else None,
        cli_task_dir=Path(os.environ.get("MOBIAGENT_TASK_DIR", "mobiagent_server/tasks")),
        cli_data_dir=Path(os.environ.get("MOBIAGENT_DATA_DIR", "mobiagent_server/data")),
        vl_base_url=os.environ.get("OPENROUTER_BASE_URL", os.environ.get("OPENAI_BASE_URL")),
        vl_api_key=os.environ.get("OPENROUTER_API_KEY", os.environ.get("OPENAI_API_KEY")),
        vl_model=os.environ.get("OPENROUTER_MODEL", os.environ.get("OPENAI_MODEL", "google/gemini-2.5-flash")),
        timeout_s=float(os.environ.get("MOBIAGENT_TIMEOUT", "30")),
    )


app = FastAPI(title="MobiAgent Gateway", version="0.1.0")


class CollectOptions(BaseModel):
    ocr_enabled: bool = True
    timeout: int = 30


class CollectRequest(BaseModel):
    task: str
    options: CollectOptions = Field(default_factory=CollectOptions)


class ActionOptions(BaseModel):
    wait_for_completion: bool = True
    timeout: int = 30


class ActionRequest(BaseModel):
    action_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    options: ActionOptions = Field(default_factory=ActionOptions)


class GatewayResponse(BaseModel):
    success: bool
    message: str
    data: dict[str, Any] | None = None


class JobStatus(BaseModel):
    job_id: str
    status: str
    result: dict[str, Any] | None = None


def _ensure_auth(authorization: Optional[str], cfg: GatewayConfig) -> None:
    if not cfg.api_key:
        return
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Authorization format")
    token = authorization[len(prefix):]
    if token != cfg.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


async def _proxy_post(url: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


def _build_task_from_action(action_type: str, params: dict[str, Any]) -> str:
    if action_type == "add_calendar_event":
        title = params.get("title", "日程")
        date = params.get("date", "")
        time_str = params.get("time", "")
        return f"打开系统日历并创建日程：{title}。日期{date} 时间{time_str}。"
    if action_type == "send_message":
        target = params.get("target", params.get("contact", "对方"))
        content = params.get("content", params.get("text", ""))
        return f"通过微信给{target}发送消息：{content}"
    if action_type == "set_reminder":
        content = params.get("content", params.get("title", "提醒事项"))
        remind_time = params.get("time", "")
        date = params.get("date", "")
        return f"在系统提醒事项中创建提醒：{content}。日期{date} 时间{remind_time}。"
    if action_type == "open_app":
        app_name = params.get("app", params.get("app_name", ""))
        return f"打开应用 {app_name}"
    return f"完成以下任务：{json.dumps({'action_type': action_type, 'params': params}, ensure_ascii=False)}"


def _dummy_image_b64() -> str:
    # 1x1 transparent PNG
    return (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMA"
        "ASsJTYQAAAAASUVORK5CYII="
    )


def _write_task_job(cfg: GatewayConfig, task: str) -> str:
    cfg.queue_dir.mkdir(parents=True, exist_ok=True)
    cfg.result_dir.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    payload = {
        "job_id": job_id,
        "created_at": time.time(),
        "task": task,
        "v1_request": {
            "task": task,
            "history": [],
            "image": _dummy_image_b64(),
        },
    }
    job_path = cfg.queue_dir / f"{job_id}.json"
    job_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return job_id


def _read_result(cfg: GatewayConfig, job_id: str) -> dict[str, Any] | None:
    result_path = cfg.result_dir / f"{job_id}.json"
    if not result_path.exists():
        return None
    try:
        return json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"raw": result_path.read_text(encoding="utf-8")}


def _wait_for_result(cfg: GatewayConfig, job_id: str, timeout_s: float) -> dict[str, Any] | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = _read_result(cfg, job_id)
        if result is not None:
            return result
        time.sleep(0.5)
    return None


def _write_task_file(cfg: GatewayConfig, task: str, output_schema: dict[str, Any] | None = None) -> tuple[Path, Path]:
    cfg.cli_task_dir.mkdir(parents=True, exist_ok=True)
    cfg.cli_data_dir.mkdir(parents=True, exist_ok=True)
    task_id = uuid.uuid4().hex
    task_path = cfg.cli_task_dir / f"{task_id}.json"
    data_dir = cfg.cli_data_dir / f"run-{task_id}"
    cmd_template = cfg.cli_cmd_template or ""
    if "runner.mobiagent.mobiagent" in cmd_template:
        # The legacy MobiAgent single-task CLI expects task_file to be a JSON list.
        payload: Any = [task]
    else:
        payload = {
            "version": "v1",
            "task_id": task_id,
            "task": task,
        }
        if output_schema:
            payload["output_schema"] = output_schema
    task_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return task_path, data_dir


def _load_json_safe(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _find_latest_image(data_dir: Path) -> Path | None:
    images = list(data_dir.glob("*.jpg"))
    if not images:
        return None
    best: tuple[int, Path] | None = None
    for img in images:
        match = re.match(r"^(\\d+)$", img.stem)
        if not match:
            continue
        idx = int(match.group(1))
        if best is None or idx > best[0]:
            best = (idx, img)
    return best[1] if best else max(images, key=lambda p: p.stat().st_mtime)


def _image_to_b64(path: Path) -> str:
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii")


def _resolve_effective_data_dir(data_dir: Path) -> Path:
    # Prefer directory that directly contains MobiAgent artifacts.
    if (data_dir / "actions.json").exists() or (data_dir / "react.json").exists():
        return data_dir
    if not data_dir.exists():
        return data_dir
    children = [p for p in data_dir.iterdir() if p.is_dir()]
    if len(children) == 1:
        child = children[0]
        if (child / "actions.json").exists() or (child / "react.json").exists():
            return child
    return data_dir


def _scan_step_files(data_dir: Path) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    pattern = re.compile(r"^(\d+)$")
    image_paths = sorted(
        [p for p in data_dir.glob("*.jpg") if pattern.match(p.stem)],
        key=lambda p: int(p.stem),
    )
    for image_path in image_paths:
        step_idx = int(image_path.stem)
        hierarchy_path = None
        for ext in ("xml", "json"):
            candidate = data_dir / f"{step_idx}.{ext}"
            if candidate.exists():
                hierarchy_path = candidate
                break
        overlays: dict[str, str] = {}
        for suffix in ("_highlighted.jpg", "_bounds.jpg", "_click_point.jpg", "_swipe.jpg"):
            overlay = data_dir / f"{step_idx}{suffix}"
            if overlay.exists():
                overlays[suffix.strip("_.jpg")] = overlay.as_posix()
        steps.append(
            {
                "step_index": step_idx,
                "image_path": image_path.as_posix(),
                "hierarchy_path": hierarchy_path.as_posix() if hierarchy_path else "",
                "overlays": overlays,
            }
        )
    return steps


def _extract_text_from_xml(path: Path) -> str:
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return ""
    texts: list[str] = []
    for node in root.iter():
        for key in ("text", "content-desc", "content_desc", "label", "name"):
            value = node.attrib.get(key)
            if value:
                texts.append(value.strip())
    seen: set[str] = set()
    deduped: list[str] = []
    for item in texts:
        if item and item not in seen:
            seen.add(item)
            deduped.append(item)
    return "\n".join(deduped)


def _collect_execution_ocr(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    by_step: list[dict[str, Any]] = []
    full_text_parts: list[str] = []
    for step in steps:
        hierarchy_path = step.get("hierarchy_path") or ""
        text = ""
        source = ""
        if hierarchy_path.endswith(".xml"):
            text = _extract_text_from_xml(Path(hierarchy_path))
            source = "hierarchy_xml"
        if text:
            full_text_parts.append(f"[step {step['step_index']}]\n{text}")
        by_step.append(
            {
                "step_index": step["step_index"],
                "text": text,
                "source": source,
            }
        )
    return by_step, "\n\n".join([t for t in full_text_parts if t]).strip()


def _status_hint_from_history(actions_obj: Any, reacts_obj: Any) -> str:
    try:
        if isinstance(actions_obj, dict):
            actions = actions_obj.get("actions", [])
            if actions and isinstance(actions[-1], dict) and actions[-1].get("type") == "done":
                if actions[-1].get("status") == "success":
                    return "completed"
                return "ended_with_done_non_success"
        if isinstance(reacts_obj, list) and reacts_obj:
            last = reacts_obj[-1]
            fn_name = (((last or {}).get("function") or {}).get("name") or "").lower()
            if fn_name == "done":
                status = (((last or {}).get("function") or {}).get("parameters") or {}).get("status")
                return "completed" if status == "success" else "ended_with_done_non_success"
    except Exception:
        pass
    return "incomplete_or_unknown"


def _build_execution_result(task: str, data_dir: Path, cli_status: str) -> dict[str, Any]:
    actions_obj = _load_json_safe(data_dir / "actions.json")
    reacts_obj = _load_json_safe(data_dir / "react.json")
    steps = _scan_step_files(data_dir)
    ocr_by_step, ocr_full = _collect_execution_ocr(steps)

    reasonings: list[str] = []
    if isinstance(reacts_obj, list):
        for item in reacts_obj:
            if isinstance(item, dict):
                reasoning = item.get("reasoning")
                if isinstance(reasoning, str) and reasoning.strip():
                    reasonings.append(reasoning.strip())

    images = [s["image_path"] for s in steps if s.get("image_path")]
    hierarchies = [s["hierarchy_path"] for s in steps if s.get("hierarchy_path")]
    overlay_paths: list[str] = []
    for step in steps:
        for path in (step.get("overlays") or {}).values():
            overlay_paths.append(path)

    action_count = 0
    last_action = ""
    task_description = task
    if isinstance(actions_obj, dict):
        action_list = actions_obj.get("actions", [])
        if isinstance(action_list, list):
            action_count = len(action_list)
            if action_list and isinstance(action_list[-1], dict):
                last_action = str(action_list[-1].get("type") or "")
        task_description = str(actions_obj.get("task_description") or task)

    status_hint = _status_hint_from_history(actions_obj, reacts_obj)
    result = {
        "schema_version": "mobi_exec_v1",
        "run_dir": data_dir.as_posix(),
        "task_description": task_description,
        "raw_cli_status": cli_status,
        "summary": {
            "step_count": len(steps),
            "action_count": action_count,
            "last_action": last_action,
            "status_hint": status_hint,
            "final_screenshot_path": images[-1] if images else "",
        },
        "artifacts": {
            "steps": steps,
            "images": images,
            "hierarchies": hierarchies,
            "overlays": overlay_paths,
        },
        "history": {
            "actions": actions_obj if actions_obj is not None else {},
            "reacts": reacts_obj if reacts_obj is not None else [],
            "reasonings": reasonings,
        },
        "ocr": {
            "source": "hierarchy_xml",
            "by_step": ocr_by_step,
            "full_text": ocr_full,
        },
    }
    index_file = data_dir / "execution_result.json"
    index_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["index_file"] = index_file.as_posix()
    return result


def _render_cli_cmd(template: str, task_path: Path, data_dir: Path) -> str:
    # Support both {task_file}/{data_dir} and <ENV_VAR> placeholders.
    cmd = template.format(task_file=task_path.as_posix(), data_dir=data_dir.as_posix())

    def _replace_env_var(match: re.Match[str]) -> str:
        name = match.group(1)
        value = os.environ.get(name)
        if value is None or value == "":
            raise HTTPException(status_code=500, detail=f"Missing env var for CLI cmd placeholder: {name}")
        return value

    cmd = re.sub(r"<([A-Z0-9_]+)>", _replace_env_var, cmd)
    stripped = cmd.lstrip()
    if stripped.startswith("python "):
        cmd = cmd.replace("python ", f"{sys.executable} ", 1)
    elif stripped.startswith("python3 "):
        cmd = cmd.replace("python3 ", f"{sys.executable} ", 1)
    return cmd


async def _call_vl_model(cfg: GatewayConfig, prompt: str, image_b64: str) -> str | None:
    if not cfg.vl_base_url or not cfg.vl_api_key or not cfg.vl_model:
        return None
    base = cfg.vl_base_url.rstrip("/")
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.vl_api_key}"}
    payload = {
        "model": cfg.vl_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            }
        ],
        "temperature": 0,
    }
    async with httpx.AsyncClient(timeout=cfg.timeout_s) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


async def _extract_output_schema(cfg: GatewayConfig, data_dir: Path, output_schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if output_schema is None:
        return None
    reacts = _load_json_safe(data_dir / "react.json")
    actions = _load_json_safe(data_dir / "actions.json")
    if not reacts or not isinstance(reacts, list):
        return None

    last_react = reacts[-1]
    action_index = None
    if isinstance(last_react, dict):
        action_index = last_react.get("action_index")
    image_path = None
    if action_index:
        candidate = data_dir / f"{action_index}.jpg"
        if candidate.exists():
            image_path = candidate
    if image_path is None:
        image_path = _find_latest_image(data_dir)
    if image_path is None:
        return None

    task_description = None
    if isinstance(actions, dict):
        task_description = actions.get("task_description")

    prompt = (
        "You are a vision-language extractor for mobile UI results.\\n"
        "Given the task description, the last reasoning step, and the final screenshot, "
        "extract the required information strictly following the output_schema. "
        "Return JSON only, no markdown.\\n"
        f"task_description: {task_description or ''}\\n"
        f"last_react: {json.dumps(last_react, ensure_ascii=False)}\\n"
        f"output_schema: {json.dumps(output_schema, ensure_ascii=False)}\\n"
    )
    image_b64 = _image_to_b64(image_path)
    vl_text = await _call_vl_model(cfg, prompt, image_b64)
    if not vl_text:
        return None
    try:
        vl_text = vl_text.strip().strip("```").strip("json").strip()
        return json.loads(vl_text)
    except json.JSONDecodeError:
        return {"raw": vl_text}


def _run_cli_job(cfg: GatewayConfig, task: str, output_schema: dict[str, Any] | None, timeout_s: float) -> dict[str, Any]:
    if not cfg.cli_cmd_template:
        raise HTTPException(status_code=500, detail="MOBIAGENT_CLI_CMD not configured")
    task_path, data_dir = _write_task_file(cfg, task, output_schema)
    task_path_abs = task_path.resolve()
    data_dir_abs = data_dir.resolve()
    cmd = _render_cli_cmd(cfg.cli_cmd_template, task_path_abs, data_dir_abs)
    workdir: Path | None = cfg.cli_workdir
    if workdir is None and " -m runner." in cmd and Path("MobiAgent").exists():
        # MobiAgent CLI frequently uses module path "runner.*" from project subdir.
        workdir = Path("MobiAgent")
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            check=False,
            timeout=timeout_s,
            capture_output=True,
            text=True,
            cwd=workdir.as_posix() if workdir else None,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "task_file": task_path.as_posix(),
            "data_dir": data_dir.as_posix(),
            "stdout": exc.stdout,
            "stderr": exc.stderr,
        }

    status = "ok" if proc.returncode == 0 else "failed"
    effective_data_dir = _resolve_effective_data_dir(data_dir_abs)
    result = {
        "status": status,
        "returncode": proc.returncode,
        "task_file": task_path_abs.as_posix(),
        "data_dir": effective_data_dir.as_posix(),
        "stdout": proc.stdout[-4000:] if proc.stdout else "",
        "stderr": proc.stderr[-4000:] if proc.stderr else "",
    }
    if status == "ok" and effective_data_dir.exists():
        result["execution"] = _build_execution_result(task, effective_data_dir, status)
    return result


@app.post("/api/v1/collect", response_model=GatewayResponse)
async def collect(request: CollectRequest, authorization: Optional[str] = Header(default=None)) -> GatewayResponse:
    cfg = load_config()
    _ensure_auth(authorization, cfg)

    if cfg.mode == "cli":
        result = _run_cli_job(cfg, request.task, None, request.options.timeout or cfg.timeout_s)
        return GatewayResponse(success=result.get("status") == "ok", message=result["status"], data=result)

    if cfg.mode == "proxy":
        if not cfg.collect_url:
            raise HTTPException(status_code=502, detail="MOBIAGENT_COLLECT_URL not configured")
        try:
            data = await _proxy_post(
                cfg.collect_url,
                {
                    "task": request.task,
                    "options": request.options.model_dump(),
                },
                cfg.timeout_s,
            )
            return GatewayResponse(success=True, message="ok", data=data.get("data", data))
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Upstream collect failed: {exc}") from exc

    if cfg.mode == "task_queue":
        job_id = _write_task_job(cfg, request.task)
        if request.options.timeout > 0:
            result = _wait_for_result(cfg, job_id, request.options.timeout)
            if result is not None:
                return GatewayResponse(success=True, message="completed", data=result)
        return GatewayResponse(success=True, message="queued", data={"job_id": job_id})

    mock_text = get_mock_collect_result(request.task)
    return GatewayResponse(
        success=True,
        message="mock",
        data={
            "ocr_text": mock_text,
            "screenshot_path": "",
            "mock_collect_result": mock_text,
            "raw": {"task": request.task, "source": "mock"},
        },
    )


@app.post("/api/v1/action", response_model=GatewayResponse)
async def action(request: ActionRequest, authorization: Optional[str] = Header(default=None)) -> GatewayResponse:
    cfg = load_config()
    _ensure_auth(authorization, cfg)

    if cfg.mode == "cli":
        task = _build_task_from_action(request.action_type, request.params)
        output_schema = request.params.get("output_schema") if isinstance(request.params, dict) else None
        result = _run_cli_job(cfg, task, output_schema, request.options.timeout or cfg.timeout_s)
        parsed = None
        if result.get("status") == "ok" and result.get("data_dir"):
            parsed = await _extract_output_schema(cfg, Path(result["data_dir"]), output_schema)
            if parsed is not None:
                result["parsed_output"] = parsed
        return GatewayResponse(success=result.get("status") == "ok", message=result["status"], data=result)

    if cfg.mode == "task_queue":
        task = _build_task_from_action(request.action_type, request.params)
        job_id = _write_task_job(cfg, task)
        if request.options.wait_for_completion:
            result = _wait_for_result(cfg, job_id, request.options.timeout or cfg.timeout_s)
            if result is not None:
                return GatewayResponse(success=True, message="completed", data=result)
            return GatewayResponse(success=False, message="pending", data={"job_id": job_id})
        return GatewayResponse(success=True, message="queued", data={"job_id": job_id})

    if cfg.mode == "proxy":
        if not cfg.action_url:
            raise HTTPException(status_code=502, detail="MOBIAGENT_ACTION_URL not configured")
        try:
            data = await _proxy_post(
                cfg.action_url,
                {
                    "action_type": request.action_type,
                    "params": request.params,
                    "options": request.options.model_dump(),
                },
                cfg.timeout_s,
            )
            message = data.get("message", "ok") if isinstance(data, dict) else "ok"
            return GatewayResponse(success=True, message=message, data=data)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Upstream action failed: {exc}") from exc

    mock_result = get_mock_action_result(request.action_type, request.params)
    return GatewayResponse(
        success=True,
        message=mock_result,
        data={"action_type": request.action_type, "params": request.params},
    )


@app.get("/")
async def health() -> dict[str, str]:
    cfg = load_config()
    return {"status": "ok", "service": "mobiagent_gateway", "mode": cfg.mode}


@app.get("/api/v1/jobs/{job_id}", response_model=JobStatus)
async def job_status(job_id: str) -> JobStatus:
    cfg = load_config()
    result = _read_result(cfg, job_id)
    if result is None:
        return JobStatus(job_id=job_id, status="pending", result=None)
    return JobStatus(job_id=job_id, status="completed", result=result)


@app.post("/api/v1/jobs/{job_id}/result")
async def upload_job_result(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    cfg.result_dir.mkdir(parents=True, exist_ok=True)
    result_path = cfg.result_dir / f"{job_id}.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {"status": "ok", "job_id": job_id}


def main() -> None:
    import uvicorn

    port = int(os.environ.get("MOBIAGENT_GATEWAY_PORT", "8081"))
    uvicorn.run("mobiagent_server.server:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
