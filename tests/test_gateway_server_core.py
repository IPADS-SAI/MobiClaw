from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi import UploadFile

from mobiclaw import gateway_server
from mobiclaw.gateway_server import feishu as feishu_module
from mobiclaw.gateway_server import EnvStructuredRequest, GatewayConfig, JobContext, TaskRequest


def _cfg(*, api_key: str = "") -> GatewayConfig:
    return GatewayConfig(
        api_key=api_key,
        callback_timeout_s=3.0,
        callback_retry=1,
        callback_retry_backoff_s=0.1,
        public_base_url="https://gw.example",
        file_root=None,
        feishu_app_id="",
        feishu_app_secret="",
        feishu_verification_token="",
        feishu_encrypt_key="",
        feishu_event_transport="off",
        feishu_native_file_enabled=True,
        feishu_native_image_enabled=True,
        feishu_ack_enabled=True,
        feishu_group_require_mention=True,
        feishu_bot_open_id="",
    )


def test_ensure_auth_all_paths() -> None:
    gateway_server._ensure_auth(None, _cfg(api_key=""))

    with pytest.raises(HTTPException) as exc_missing:
        gateway_server._ensure_auth(None, _cfg(api_key="k1"))
    assert exc_missing.value.status_code == 401

    with pytest.raises(HTTPException) as exc_format:
        gateway_server._ensure_auth("Token k1", _cfg(api_key="k1"))
    assert exc_format.value.status_code == 401

    with pytest.raises(HTTPException) as exc_invalid:
        gateway_server._ensure_auth("Bearer bad", _cfg(api_key="k1"))
    assert exc_invalid.value.status_code == 401

    gateway_server._ensure_auth("Bearer k1", _cfg(api_key="k1"))


def test_resolve_context_id_priority_and_fallback() -> None:
    assert gateway_server._resolve_context_id(" explicit id ", None) == "explicit-id"

    result = {
        "context_id": "ctx-1",
        "session_id": "sess-1",
        "session": {"context_id": "nested-ctx", "session_id": "nested-sess"},
    }
    assert gateway_server._resolve_context_id(None, result) == "ctx-1"

    nested_only = {"session": {"session_id": "nested-sess"}}
    assert gateway_server._resolve_context_id(None, nested_only) == "nested-sess"

    assert gateway_server._resolve_context_id(None, None) is None


def test_append_chat_history_and_read_recent_messages(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "session"
    monkeypatch.setattr(gateway_server, "_chat_session_root_dir", lambda: root)

    gateway_server._append_chat_history(
        context_id="ctx_a",
        mode="worker",
        job_id="job-1",
        user_text="hello",
        assistant_text="world",
        status="completed",
    )
    gateway_server._append_chat_history(
        context_id="ctx_a",
        mode="worker",
        job_id="job-2",
        user_text="only-user",
        assistant_text="",
        status="completed",
    )

    session_dir = gateway_server._latest_session_dir_for_context("ctx_a")
    assert session_dir is not None

    messages = gateway_server._read_recent_session_messages(session_dir, limit=20)
    assert len(messages) == 3
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[2]["text"] == "only-user"
    assert messages[0]["meta"]["job_id"] == "job-1"


def test_parse_split_and_render_env_content() -> None:
    content = """
# comment
export OPENROUTER_API_KEY="k"
MOBICLAW_LOG_LEVEL=DEBUG
CUSTOM=42
INVALID_LINE
"""
    variables = gateway_server._parse_env_variables(content)
    assert variables["OPENROUTER_API_KEY"] == "k"
    assert variables["MOBICLAW_LOG_LEVEL"] == "DEBUG"
    assert variables["CUSTOM"] == "42"
    assert "INVALID_LINE" not in variables

    managed, unmanaged = gateway_server._split_env_variables(variables)
    assert managed["OPENROUTER_API_KEY"] == "k"
    assert unmanaged["CUSTOM"] == "42"

    rendered = gateway_server._render_structured_env_content(
        values={"MOBICLAW_LOG_LEVEL": "INFO", "OPENROUTER_API_KEY": "abc"},
        unmanaged={"CUSTOM": "42"},
    )
    assert "export MOBICLAW_LOG_LEVEL=\"INFO\"" in rendered
    assert "export OPENROUTER_API_KEY=\"abc\"" in rendered
    assert "export CUSTOM=\"42\"" in rendered


def test_file_exposure_and_result_decoration(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir(parents=True, exist_ok=True)
    allowed = root / "ok.txt"
    allowed.write_text("ok", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("out", encoding="utf-8")

    cfg = GatewayConfig(
        api_key="",
        callback_timeout_s=3.0,
        callback_retry=1,
        callback_retry_backoff_s=0.1,
        public_base_url="https://gw.example",
        file_root=str(root),
        feishu_app_id="",
        feishu_app_secret="",
        feishu_verification_token="",
        feishu_encrypt_key="",
        feishu_event_transport="off",
        feishu_native_file_enabled=True,
        feishu_native_image_enabled=True,
        feishu_ack_enabled=True,
        feishu_group_require_mention=True,
        feishu_bot_open_id="",
    )

    assert gateway_server._can_expose_file(str(allowed), cfg) is True
    assert gateway_server._can_expose_file(str(outside), cfg) is False

    result = {
        "reply": "done",
        "files": [
            {"name": "ok.txt", "path": str(allowed)},
            {"name": "outside.txt", "path": str(outside)},
            {"name": "bad-no-path"},
        ],
    }
    decorated = gateway_server._decorate_result_with_files("job-9", result, request=None, cfg=cfg)
    files = decorated["files"]
    assert len(files) == 1
    assert files[0]["name"] == "ok.txt"
    assert files[0]["download_url"].endswith("/api/v1/files/job-9/ok.txt")


def test_file_exposure_allows_default_outputs_even_when_file_root_set(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    outputs_root = project_root / "outputs"
    outputs_root.mkdir(parents=True, exist_ok=True)
    output_file = outputs_root / "result.md"
    output_file.write_text("ok", encoding="utf-8")

    blocked_root = tmp_path / "blocked"
    blocked_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        gateway_server,
        "_default_exposed_roots",
        lambda: [outputs_root.resolve()],
    )

    cfg = GatewayConfig(
        api_key="",
        callback_timeout_s=3.0,
        callback_retry=1,
        callback_retry_backoff_s=0.1,
        public_base_url="https://gw.example",
        file_root=str(blocked_root),
        feishu_app_id="",
        feishu_app_secret="",
        feishu_verification_token="",
        feishu_encrypt_key="",
        feishu_event_transport="off",
        feishu_native_file_enabled=True,
        feishu_native_image_enabled=True,
        feishu_ack_enabled=True,
        feishu_group_require_mention=True,
        feishu_bot_open_id="",
    )

    assert gateway_server._can_expose_file(str(output_file), cfg) is True


def test_build_callback_headers() -> None:
    headers = gateway_server._build_callback_headers(
        JobContext(
            webhook_token="tok",
            callback_headers={"X-Trace": "1", "X-Empty": "", "": "bad"},
        )
    )
    assert headers["Content-Type"] == "application/json"
    assert headers["Authorization"] == "Bearer tok"
    assert headers["X-Trace"] == "1"
    assert "X-Empty" not in headers


def test_run_job_success_non_chat(monkeypatch) -> None:
    job_id = "job-ok"
    gateway_server._JOB_STORE.clear()
    gateway_server._JOB_CONTEXT.clear()
    gateway_server._JOB_CONTEXT[job_id] = JobContext()

    append_calls: list[dict] = []
    deliver_calls: list[str] = []

    async def fake_run_gateway_task(**kwargs):  # noqa: ANN003
        progress = kwargs.get("progress_callback")
        assert progress is not None
        await progress({"channel": "planner_monitor", "session_id": "ctx-a", "mode": "worker", "planner": {"enabled": True}})
        return {"reply": "done", "files": []}

    async def fake_deliver(job: str, result, cfg):  # noqa: ANN001
        del result, cfg
        deliver_calls.append(job)

    def fake_append(**kwargs):  # noqa: ANN003
        append_calls.append(kwargs)

    monkeypatch.setattr(gateway_server, "run_gateway_task", fake_run_gateway_task)
    monkeypatch.setattr(gateway_server, "_deliver_result", fake_deliver)
    monkeypatch.setattr(gateway_server, "_append_chat_history", fake_append)
    monkeypatch.setattr(gateway_server, "_decorate_result_with_files", lambda job_id, result, request, cfg: {**result, "decorated": True})
    monkeypatch.setattr(gateway_server, "load_config", lambda: _cfg())

    import mobiclaw.config as seneschal_config

    monkeypatch.setitem(seneschal_config.RAG_CONFIG, "task_history_enabled", False)

    asyncio.run(
        gateway_server._run_job(
            job_id=job_id,
            task="do it",
            output_path=None,
            mode="worker",
            agent_hint=None,
            skill_hint=None,
            routing_strategy=None,
            context_id="ctx-a",
            web_search_enabled=False,
        )
    )

    stored = gateway_server._JOB_STORE[job_id]
    assert stored.status == "completed"
    assert stored.result is not None
    assert stored.result["context_id"] == "ctx-a"
    assert stored.result["decorated"] is True
    assert len(append_calls) == 0
    assert deliver_calls == [job_id]


def test_run_job_failure_non_chat(monkeypatch) -> None:
    job_id = "job-fail"
    gateway_server._JOB_STORE.clear()
    gateway_server._JOB_CONTEXT.clear()
    gateway_server._JOB_CONTEXT[job_id] = JobContext()

    append_calls: list[dict] = []
    deliver_calls: list[str] = []

    async def fake_run_gateway_task(**kwargs):  # noqa: ANN003
        del kwargs
        raise RuntimeError("boom")

    async def fake_deliver(job: str, result, cfg):  # noqa: ANN001
        del result, cfg
        deliver_calls.append(job)

    def fake_append(**kwargs):  # noqa: ANN003
        append_calls.append(kwargs)

    monkeypatch.setattr(gateway_server, "run_gateway_task", fake_run_gateway_task)
    monkeypatch.setattr(gateway_server, "_deliver_result", fake_deliver)
    monkeypatch.setattr(gateway_server, "_append_chat_history", fake_append)
    monkeypatch.setattr(gateway_server, "load_config", lambda: _cfg())

    asyncio.run(
        gateway_server._run_job(
            job_id=job_id,
            task="do it",
            output_path=None,
            mode="worker",
            agent_hint=None,
            skill_hint=None,
            routing_strategy=None,
            context_id="ctx-x",
            web_search_enabled=False,
        )
    )

    stored = gateway_server._JOB_STORE[job_id]
    assert stored.status == "failed"
    assert stored.error == "boom"
    assert stored.result is not None
    assert stored.result["context_id"] == "ctx-x"
    assert len(append_calls) == 0
    assert deliver_calls == [job_id]


def test_submit_task_sync_non_chat(monkeypatch) -> None:
    append_calls: list[dict] = []
    captured: dict = {}

    async def fake_run_gateway_task(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {"reply": "ok", "files": []}

    def fake_append(**kwargs):  # noqa: ANN003
        append_calls.append(kwargs)

    monkeypatch.setattr(gateway_server, "run_gateway_task", fake_run_gateway_task)
    monkeypatch.setattr(gateway_server, "_append_chat_history", fake_append)
    monkeypatch.setattr(gateway_server, "_decorate_result_with_files", lambda job_id, result, request, cfg: {**result, "decorated": True})
    monkeypatch.setattr(gateway_server, "load_config", lambda: _cfg(api_key=""))

    req = TaskRequest(
        task="hello",
        async_mode=False,
        mode="worker",
        context_id="ctx-42",
        input_files=["/tmp/a.txt", "/tmp/b.md"],
    )
    raw_request = SimpleNamespace(base_url="https://gw.example/")

    result = asyncio.run(gateway_server.submit_task(req, raw_request=raw_request, authorization=None))

    assert result.status == "completed"
    assert result.result is not None
    assert result.result["reply"] == "ok"
    assert result.result["context_id"] == "ctx-42"
    assert result.result["decorated"] is True
    assert result.result["input_files"] == ["/tmp/a.txt", "/tmp/b.md"]
    sent_task = str(captured.get("task") or "")
    assert "附加输入文件（本地路径）如下" in sent_task
    assert "- /tmp/a.txt" in sent_task
    assert len(append_calls) == 0


def test_upload_chat_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gateway_server, "load_config", lambda: _cfg(api_key=""))
    monkeypatch.setattr(gateway_server, "_chat_upload_root_dir", lambda: tmp_path / "uploads")

    upload = UploadFile(
        filename="notes.txt",
        file=io.BytesIO(b"hello gateway"),
        headers={"content-type": "text/plain"},
    )

    resp = asyncio.run(gateway_server.upload_chat_files(files=[upload], authorization=None))
    files = resp.get("files")
    assert isinstance(files, list)
    assert len(files) == 1
    item = files[0]
    assert item["name"] == "notes.txt"
    assert item["size"] == 13
    assert item["mime_type"] == "text/plain"
    assert Path(item["path"]).exists()


def test_read_recent_session_messages_skips_invalid_rows(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    history = session_dir / "history.jsonl"
    history.write_text(
        "\n".join(
            [
                "not json",
                json.dumps({"text": "", "role": "user"}, ensure_ascii=False),
                json.dumps({"text": "ok1", "role": "unknown", "name": "n"}, ensure_ascii=False),
                json.dumps({"text": "ok2", "role": "assistant", "meta": "bad"}, ensure_ascii=False),
            ]
        ),
        encoding="utf-8",
    )

    messages = gateway_server._read_recent_session_messages(session_dir, limit=20)
    assert len(messages) == 2
    assert messages[0]["role"] == "assistant"
    assert messages[1]["meta"] == {}


def test_get_file_happy_and_forbidden(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir(parents=True, exist_ok=True)
    allowed = root / "result.txt"
    allowed.write_text("ok", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("no", encoding="utf-8")

    gateway_server._JOB_STORE.clear()
    gateway_server._JOB_STORE["job-1"] = gateway_server.TaskResult(
        job_id="job-1",
        status="completed",
        result={
            "files": [
                {"name": "result.txt", "path": str(allowed)},
                {"name": "outside.txt", "path": str(outside)},
            ]
        },
    )

    monkeypatch.setattr(
        gateway_server,
        "load_config",
        lambda: GatewayConfig(
            api_key="",
            callback_timeout_s=3.0,
            callback_retry=1,
            callback_retry_backoff_s=0.1,
            public_base_url=None,
            file_root=str(root),
            feishu_app_id="",
            feishu_app_secret="",
            feishu_verification_token="",
            feishu_encrypt_key="",
            feishu_event_transport="off",
            feishu_native_file_enabled=True,
            feishu_native_image_enabled=True,
            feishu_ack_enabled=True,
            feishu_group_require_mention=True,
            feishu_bot_open_id="",
        ),
    )

    ok = asyncio.run(gateway_server.get_file("job-1", "result.txt", authorization=None))
    assert ok.filename == "result.txt"
    assert str(allowed.resolve()) in str(ok.path)

    with pytest.raises(HTTPException) as exc_forbidden:
        asyncio.run(gateway_server.get_file("job-1", "outside.txt", authorization=None))
    assert exc_forbidden.value.status_code == 403

    with pytest.raises(HTTPException) as exc_not_found:
        asyncio.run(gateway_server.get_file("job-1", "missing.txt", authorization=None))
    assert exc_not_found.value.status_code == 404


def test_put_env_schema_preserve_unmanaged(monkeypatch, tmp_path: Path) -> None:
    saved_content = {"value": ""}
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    def fake_read() -> str:
        if saved_content["value"]:
            return saved_content["value"]
        return "OPENROUTER_API_KEY=old\nCUSTOM_X=1\n"

    def fake_write(content: str) -> None:
        saved_content["value"] = content
        env_file.write_text(content, encoding="utf-8")

    monkeypatch.setattr(gateway_server, "load_config", lambda: _cfg(api_key=""))
    monkeypatch.setattr(gateway_server, "_read_env_content", fake_read)
    monkeypatch.setattr(gateway_server, "_write_env_content", fake_write)
    monkeypatch.setattr(gateway_server, "_env_file_path", lambda: env_file)

    req = EnvStructuredRequest(
        values={"OPENROUTER_API_KEY": "new-key", "MOBICLAW_LOG_LEVEL": "INFO"},
        unmanaged=None,
        preserve_unmanaged=True,
    )
    payload = asyncio.run(gateway_server.put_env_schema(req, authorization=None))

    assert payload["ok"] is True
    assert payload["values"]["OPENROUTER_API_KEY"] == "new-key"
    assert payload["unmanaged"]["CUSTOM_X"] == "1"
    assert "export OPENROUTER_API_KEY=\"new-key\"" in saved_content["value"]
    assert "export CUSTOM_X=\"1\"" in saved_content["value"]


class _DummyFeishuRequest:
    def __init__(self, payload: dict, raw: bytes | None = None) -> None:
        self._payload = payload
        self._raw = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")

    async def body(self) -> bytes:
        return self._raw

    async def json(self) -> dict:
        return self._payload


def test_feishu_events_url_verification(monkeypatch) -> None:
    monkeypatch.setattr(gateway_server, "load_config", lambda: _cfg(api_key=""))
    req = _DummyFeishuRequest({"type": "url_verification", "challenge": "abc"})

    result = asyncio.run(gateway_server.feishu_events(req))
    assert result == {"challenge": "abc"}


def test_feishu_events_invalid_signature(monkeypatch) -> None:
    monkeypatch.setattr(gateway_server, "load_config", lambda: _cfg(api_key=""))
    monkeypatch.setattr(gateway_server, "_verify_feishu_signature", lambda **kwargs: False)
    req = _DummyFeishuRequest({"type": "event_callback"})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(gateway_server.feishu_events(req))
    assert exc.value.status_code == 401


def test_feishu_events_invalid_token(monkeypatch) -> None:
    cfg = _cfg(api_key="")
    cfg.feishu_verification_token = "expected"
    monkeypatch.setattr(gateway_server, "load_config", lambda: cfg)
    req = _DummyFeishuRequest({"type": "event_callback", "token": "bad"})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(gateway_server.feishu_events(req))
    assert exc.value.status_code == 401


def test_feishu_events_message_accept(monkeypatch) -> None:
    cfg = _cfg(api_key="")
    cfg.feishu_verification_token = "ok-token"
    monkeypatch.setattr(gateway_server, "load_config", lambda: cfg)

    accepted_args: dict = {}

    async def fake_accept_feishu_message(**kwargs):  # noqa: ANN003
        accepted_args.update(kwargs)
        return {"ok": True, "accepted": True, "job_id": "j1"}

    monkeypatch.setattr(gateway_server, "_accept_feishu_message", fake_accept_feishu_message)

    req = _DummyFeishuRequest(
        {
            "type": "event_callback",
            "token": "ok-token",
            "event": {
                "message": {
                    "content": "{\"text\":\"hello\"}",
                    "chat_id": "chat-1",
                    "message_id": "msg-1",
                },
                "sender": {
                    "sender_id": {
                        "open_id": "ou_1",
                    }
                },
            },
        }
    )

    result = asyncio.run(gateway_server.feishu_events(req))
    assert result["accepted"] is True
    assert accepted_args["chat_id"] == "chat-1"
    assert accepted_args["open_id"] == "ou_1"
    assert accepted_args["message_id"] == "msg-1"


def test_should_accept_feishu_message_reject_when_bot_id_unavailable() -> None:
    cfg = _cfg(api_key="")
    accepted, reason = gateway_server._should_accept_feishu_message(
        cfg,
        chat_type="group",
        content='{"text":"<at user_id=\\"ou_other\\\">X</at> hi"}',
        mentions=[{"id": {"open_id": "ou_other"}}],
    )
    assert accepted is False
    assert reason == "bot_open_id_unavailable"


def test_should_accept_feishu_message_only_accepts_bot_mention(monkeypatch) -> None:
    cfg = _cfg(api_key="")
    monkeypatch.setattr(feishu_module, "_resolve_feishu_bot_open_id", lambda _cfg: "ou_bot")

    accepted_other, reason_other = gateway_server._should_accept_feishu_message(
        cfg,
        chat_type="group",
        content='{"text":"hi"}',
        mentions=[{"id": {"open_id": "ou_other"}}],
    )
    assert accepted_other is False
    assert reason_other == "mentioned_other_user_not_bot"

    accepted_bot, reason_bot = gateway_server._should_accept_feishu_message(
        cfg,
        chat_type="group",
        content='{"text":"hi"}',
        mentions=[{"id": {"open_id": "ou_bot"}}],
    )
    assert accepted_bot is True
    assert reason_bot is None
