# -*- coding: utf-8 -*-
"""Seneschal gateway server for task intake."""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from agentscope.message import Msg

from .agents import create_steward_agent


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(Path(__file__).resolve().parents[1] / ".env")


@dataclass
class GatewayConfig:
    api_key: str


def load_config() -> GatewayConfig:
    return GatewayConfig(api_key=os.environ.get("SENESCHAL_GATEWAY_API_KEY", ""))


class TaskRequest(BaseModel):
    task: str
    async_mode: bool = Field(default=False)


class TaskResult(BaseModel):
    job_id: str
    status: str
    result: dict[str, Any] | None = None


app = FastAPI(title="Seneschal Gateway", version="0.1.0")

_JOB_STORE: dict[str, TaskResult] = {}
_JOB_LOCK = asyncio.Lock()


def _ensure_auth(authorization: str | None, cfg: GatewayConfig) -> None:
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


async def _run_task(task: str) -> dict[str, Any]:
    steward = create_steward_agent()
    msg = Msg(name="User", content=task, role="user")
    response = await steward(msg)
    text = response.get_text_content() if response else ""
    return {"reply": text}


async def _run_job(job_id: str, task: str) -> None:
    try:
        result = await _run_task(task)
        async with _JOB_LOCK:
            _JOB_STORE[job_id] = TaskResult(job_id=job_id, status="completed", result=result)
    except Exception as exc:
        async with _JOB_LOCK:
            _JOB_STORE[job_id] = TaskResult(
                job_id=job_id,
                status="failed",
                result={"error": str(exc)},
            )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/task", response_model=TaskResult)
async def submit_task(
    request: TaskRequest,
    authorization: str | None = Header(default=None),
) -> TaskResult:
    cfg = load_config()
    _ensure_auth(authorization, cfg)

    if not request.task.strip():
        raise HTTPException(status_code=400, detail="Task must not be empty")

    if request.async_mode:
        job_id = uuid.uuid4().hex
        async with _JOB_LOCK:
            _JOB_STORE[job_id] = TaskResult(job_id=job_id, status="running")
        asyncio.create_task(_run_job(job_id, request.task))
        return _JOB_STORE[job_id]

    job_id = uuid.uuid4().hex
    result = await _run_task(request.task)
    return TaskResult(job_id=job_id, status="completed", result=result)


@app.get("/api/v1/jobs/{job_id}", response_model=TaskResult)
async def get_job(job_id: str) -> TaskResult:
    async with _JOB_LOCK:
        job = _JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("SENESCHAL_GATEWAY_HOST", "0.0.0.0")
    port = int(os.environ.get("SENESCHAL_GATEWAY_PORT", "8090"))
    uvicorn.run("seneschal.gateway_server:app", host=host, port=port, reload=False)
