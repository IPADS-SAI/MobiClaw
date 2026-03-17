from __future__ import annotations

import asyncio

from mobiclaw.tools.papers import download_file


class _DummyResp:
    def __init__(self, status_code: int, payload: dict | None = None, chunks: list[bytes] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self._chunks = chunks or []
        self.content = b"{}"

    def json(self):  # noqa: ANN201
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http error status={self.status_code}")

    def iter_content(self, chunk_size: int = 8192):  # noqa: ANN001, ANN201
        _ = chunk_size
        for chunk in self._chunks:
            yield chunk


def test_download_file_does_not_inject_custom_headers(monkeypatch, tmp_path) -> None:

    calls: list[tuple[str, dict]] = []

    def _fake_get(url: str, **kwargs):  # noqa: ANN003, ANN201
        calls.append((url, kwargs))
        assert kwargs.get("headers") is None
        return _DummyResp(200, chunks=[b"hello ", b"world"])

    monkeypatch.setattr("mobiclaw.tools.papers.requests.get", _fake_get)

    output = tmp_path / "paper.pdf"
    result = asyncio.run(
        download_file(
            url="https://example.com/paper.pdf",
            output_path=str(output),
        )
    )

    assert output.read_bytes() == b"hello world"
    assert result.metadata.get("bytes") == 11
