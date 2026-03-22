from __future__ import annotations

from pathlib import Path

from mobiclaw.tools.feishu import fetch_feishu_chat_history


class _DummyResp:
    def __init__(self, status_code: int, payload: dict, chunks: list[bytes] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self._chunks = chunks or []
        self.content = b"{}"
        self.text = ""

    def json(self):  # noqa: ANN201
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http error status={self.status_code}")

    def iter_content(self, chunk_size: int = 8192):  # noqa: ANN001, ANN201
        _ = chunk_size
        for chunk in self._chunks:
            yield chunk


def test_fetch_feishu_chat_history_parses_media_content(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_xxx")
    monkeypatch.chdir(tmp_path)

    def _fake_post(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "tenant_access_token" in url:
            return _DummyResp(200, {"code": 0, "tenant_access_token": "t-1"})
        raise AssertionError(f"unexpected url: {url}")

    def _fake_get(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "im/v1/messages" in url and "/resources/" not in url:
            return _DummyResp(
                200,
                {
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "items": [
                            {
                                "message_id": "om_text",
                                "create_time": "1710000000000",
                                "sender": {"id": "ou_x"},
                                "msg_type": "text",
                                "body": {"content": '{"text":"hello"}'},
                            },
                            {
                                "message_id": "om_img",
                                "create_time": "1710000001000",
                                "sender": {"id": "ou_x"},
                                "msg_type": "image",
                                "body": {"content": '{"image_key":"img_v2_xxx"}'},
                            },
                            {
                                "message_id": "om_file",
                                "create_time": "1710000002000",
                                "sender": {"id": "ou_x"},
                                "msg_type": "file",
                                "body": {"content": '{"file_key":"file_v2_xxx","file_name":"report.pdf"}'},
                            },
                        ],
                        "has_more": False,
                        "page_token": "",
                    },
                },
            )
        if "/resources/" in url:
            resource_type = "file" if "?type=file" in url else "image"
            payload = b"%PDF-1.4" if resource_type == "file" else b"PNG"
            return _DummyResp(200, {}, chunks=[payload])
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("mobiclaw.tools.feishu.requests.post", _fake_post)
    monkeypatch.setattr("mobiclaw.tools.feishu.requests.get", _fake_get)

    result = fetch_feishu_chat_history(
        chat_id="oc_1234567890abcdef1234567890abcdef",
        output_file_dir=str(tmp_path / "outputs" / "job_test" / "tmp"),
        download_files=True,
        download_images=True,
        history_range="all",
        page_size=20,
    )

    messages = result.metadata.get("messages") or []
    assert len(messages) == 3

    text_msg = messages[0]
    assert text_msg.get("content_type") == "text"
    assert text_msg.get("text") == "hello"

    image_msg = messages[1]
    assert image_msg.get("content_type") == "image"
    assert image_msg.get("image_key") == "img_v2_xxx"
    image_local_path = str(image_msg.get("local_path") or "")
    assert image_local_path
    assert Path(image_local_path).exists()

    file_msg = messages[2]
    assert file_msg.get("content_type") == "file"
    assert file_msg.get("file_key") == "file_v2_xxx"
    assert file_msg.get("file_name") == "report.pdf"
    file_local_path = str(file_msg.get("local_path") or "")
    assert file_local_path
    assert Path(file_local_path).exists()

    attachments = result.metadata.get("attachments") or []
    files = result.metadata.get("files") or []
    images = result.metadata.get("images") or []
    download_dir = Path(str(result.metadata.get("download_dir") or ""))
    assert len(attachments) == 2
    assert len(files) == 1
    assert len(images) == 1
    assert files[0].get("file_key") == "file_v2_xxx"
    assert images[0].get("image_key") == "img_v2_xxx"
    assert download_dir.name == "feishu_media"
    assert download_dir.parts[-3:] == ("outputs", "job_test", "feishu_media")


def test_fetch_feishu_chat_history_parses_plain_and_json_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_xxx")

    def _fake_post(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "tenant_access_token" in url:
            return _DummyResp(200, {"code": 0, "tenant_access_token": "t-1"})
        raise AssertionError(f"unexpected url: {url}")

    def _fake_get(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "im/v1/messages" in url:
            return _DummyResp(
                200,
                {
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "items": [
                            {
                                "message_id": "om_plain",
                                "create_time": "1710000100000",
                                "sender": {"id": "ou_x"},
                                "msg_type": "unknown",
                                "body": {"content": "plain text payload"},
                            },
                            {
                                "message_id": "om_json",
                                "create_time": "1710000101000",
                                "sender": {"id": "ou_x"},
                                "msg_type": "unknown",
                                "body": {"content": '{"foo":"bar"}'},
                            },
                        ],
                        "has_more": False,
                        "page_token": "",
                    },
                },
            )
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("mobiclaw.tools.feishu.requests.post", _fake_post)
    monkeypatch.setattr("mobiclaw.tools.feishu.requests.get", _fake_get)

    result = fetch_feishu_chat_history(
        chat_id="oc_1234567890abcdef1234567890abcdef",
        output_file_dir=str(tmp_path / "outputs" / "job_test" / "tmp"),
        history_range="all",
        page_size=20,
    )

    messages = result.metadata.get("messages") or []
    assert len(messages) == 2

    plain_msg = messages[0]
    assert plain_msg.get("content_type") == "plain"
    assert plain_msg.get("text") == "plain text payload"

    json_msg = messages[1]
    assert json_msg.get("content_type") == "json"
    assert (json_msg.get("content_json") or {}).get("foo") == "bar"


def test_fetch_feishu_chat_history_downloads_special_file_keys(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_xxx")
    monkeypatch.chdir(tmp_path)

    def _fake_post(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "tenant_access_token" in url:
            return _DummyResp(200, {"code": 0, "tenant_access_token": "t-1"})
        raise AssertionError(f"unexpected url: {url}")

    def _fake_get(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "im/v1/messages" in url and "/resources/" not in url:
            return _DummyResp(
                200,
                {
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "items": [
                            {
                                "message_id": "om_file",
                                "create_time": "1710000002000",
                                "sender": {"id": "ou_x"},
                                "msg_type": "file",
                                "body": {"content": '{"file_key":"file_v3_ab/+/=","file_name":"report.pdf"}'},
                            },
                        ],
                        "has_more": False,
                        "page_token": "",
                    },
                },
            )
        if "/resources/" in url:
            assert "file_v3_ab%2F%2B%2F%3D" in url
            return _DummyResp(200, {}, chunks=[b"ok"])
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("mobiclaw.tools.feishu.requests.post", _fake_post)
    monkeypatch.setattr("mobiclaw.tools.feishu.requests.get", _fake_get)

    result = fetch_feishu_chat_history(
        chat_id="oc_1234567890abcdef1234567890abcdef",
        output_file_dir=str(tmp_path / "outputs" / "job_test" / "tmp"),
        download_files=True,
        history_range="all",
        page_size=20,
    )

    files = result.metadata.get("files") or []
    assert len(files) == 1
    local_path = str(files[0].get("local_path") or "")
    assert local_path
    assert Path(local_path).exists()


def test_fetch_feishu_chat_history_default_no_media_download(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_xxx")

    resource_calls = 0

    def _fake_post(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "tenant_access_token" in url:
            return _DummyResp(200, {"code": 0, "tenant_access_token": "t-1"})
        raise AssertionError(f"unexpected url: {url}")

    def _fake_get(url: str, **kwargs):  # noqa: ANN003, ANN201
        nonlocal resource_calls
        if "im/v1/messages" in url and "/resources/" not in url:
            return _DummyResp(
                200,
                {
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "items": [
                            {
                                "message_id": "om_img",
                                "create_time": "1710000001000",
                                "sender": {"id": "ou_x"},
                                "msg_type": "image",
                                "body": {"content": '{"image_key":"img_v2_xxx"}'},
                            },
                            {
                                "message_id": "om_file",
                                "create_time": "1710000002000",
                                "sender": {"id": "ou_x"},
                                "msg_type": "file",
                                "body": {"content": '{"file_key":"file_v2_xxx","file_name":"report.pdf"}'},
                            },
                        ],
                        "has_more": False,
                        "page_token": "",
                    },
                },
            )
        if "/resources/" in url:
            resource_calls += 1
            return _DummyResp(200, {}, chunks=[b"unexpected"])
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("mobiclaw.tools.feishu.requests.post", _fake_post)
    monkeypatch.setattr("mobiclaw.tools.feishu.requests.get", _fake_get)

    result = fetch_feishu_chat_history(
        chat_id="oc_1234567890abcdef1234567890abcdef",
        output_file_dir=str(tmp_path / "outputs" / "job_test" / "tmp"),
        history_range="all",
        page_size=20,
    )

    assert resource_calls == 0
    files = result.metadata.get("files") or []
    images = result.metadata.get("images") or []
    assert len(files) == 1
    assert len(images) == 1
    assert not str(files[0].get("local_path") or "")
    assert not str(images[0].get("local_path") or "")
