from __future__ import annotations

import requests

from mobiclaw.tools.feishu import read_feishu_docx_link


class _DummyResp:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}"
        self.text = ""

    def json(self):  # noqa: ANN201
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http error status={self.status_code}")


def test_read_feishu_docx_link_success(monkeypatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_xxx")

    def _fake_post(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "tenant_access_token" in url:
            return _DummyResp(200, {"code": 0, "tenant_access_token": "t-1"})
        raise AssertionError(f"unexpected url: {url}")

    def _fake_get(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "docx/v1/documents" in url:
            return _DummyResp(
                200,
                {
                    "code": 0,
                    "data": {
                        "document": {"title": "项目周报"},
                        "content": "第一段\n参考链接: https://example.com/a",
                    },
                },
            )
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("mobiclaw.tools.feishu.requests.post", _fake_post)
    monkeypatch.setattr("mobiclaw.tools.feishu.requests.get", _fake_get)

    result = read_feishu_docx_link("https://ncnq5si21nqm.feishu.cn/docx/NqbjdxRz4o147qxm1WycRmDCnze")

    assert result.metadata.get("title") == "项目周报"
    assert result.metadata.get("doc_token") == "NqbjdxRz4o147qxm1WycRmDCnze"
    assert result.metadata.get("http_status") == 200
    assert result.metadata.get("truncated") is False
    assert "第一段" in str(result.metadata.get("text") or "")
    assert "https://example.com/a" in (result.metadata.get("links") or [])


def test_read_feishu_docx_link_invalid_url() -> None:
    result = read_feishu_docx_link("https://example.com/not-feishu")
    assert result.metadata.get("error") == "unsupported_doc_url"


def test_read_feishu_docx_link_forbidden(monkeypatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_xxx")

    def _fake_post(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "tenant_access_token" in url:
            return _DummyResp(200, {"code": 0, "tenant_access_token": "t-1"})
        raise AssertionError(f"unexpected url: {url}")

    def _fake_get(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "docx/v1/documents" in url:
            return _DummyResp(403, {"code": 99991663, "msg": "forbidden"})
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("mobiclaw.tools.feishu.requests.post", _fake_post)
    monkeypatch.setattr("mobiclaw.tools.feishu.requests.get", _fake_get)

    result = read_feishu_docx_link("https://ncnq5si21nqm.feishu.cn/docx/NqbjdxRz4o147qxm1WycRmDCnze")
    assert result.metadata.get("error") == "forbidden"
    assert result.metadata.get("http_status") == 403


def test_read_feishu_docx_link_truncation(monkeypatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_xxx")

    long_text = "x" * 1500

    def _fake_post(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "tenant_access_token" in url:
            return _DummyResp(200, {"code": 0, "tenant_access_token": "t-1"})
        raise AssertionError(f"unexpected url: {url}")

    def _fake_get(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "docx/v1/documents" in url:
            return _DummyResp(200, {"code": 0, "data": {"content": long_text}})
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("mobiclaw.tools.feishu.requests.post", _fake_post)
    monkeypatch.setattr("mobiclaw.tools.feishu.requests.get", _fake_get)

    result = read_feishu_docx_link(
        "https://ncnq5si21nqm.feishu.cn/docx/NqbjdxRz4o147qxm1WycRmDCnze",
        max_length=1000,
    )
    text = str(result.metadata.get("text") or "")
    assert result.metadata.get("truncated") is True
    assert len(text) <= 1000
    assert text.endswith("...")


def test_read_feishu_docx_link_empty_content(monkeypatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_xxx")

    def _fake_post(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "tenant_access_token" in url:
            return _DummyResp(200, {"code": 0, "tenant_access_token": "t-1"})
        raise AssertionError(f"unexpected url: {url}")

    def _fake_get(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "docx/v1/documents" in url:
            return _DummyResp(200, {"code": 0, "data": {}})
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("mobiclaw.tools.feishu.requests.post", _fake_post)
    monkeypatch.setattr("mobiclaw.tools.feishu.requests.get", _fake_get)

    result = read_feishu_docx_link("https://ncnq5si21nqm.feishu.cn/docx/NqbjdxRz4o147qxm1WycRmDCnze")
    assert result.metadata.get("error") == "empty_content"


def test_read_feishu_docx_link_request_exception(monkeypatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_xxx")

    def _fake_post(url: str, **kwargs):  # noqa: ANN003, ANN201
        if "tenant_access_token" in url:
            return _DummyResp(200, {"code": 0, "tenant_access_token": "t-1"})
        raise AssertionError(f"unexpected url: {url}")

    def _fake_get(url: str, **kwargs):  # noqa: ANN003, ANN201
        raise requests.Timeout("timed out")

    monkeypatch.setattr("mobiclaw.tools.feishu.requests.post", _fake_post)
    monkeypatch.setattr("mobiclaw.tools.feishu.requests.get", _fake_get)

    result = read_feishu_docx_link("https://ncnq5si21nqm.feishu.cn/docx/NqbjdxRz4o147qxm1WycRmDCnze")
    assert result.metadata.get("error") == "request_exception"
