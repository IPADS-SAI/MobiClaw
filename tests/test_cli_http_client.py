"""Tests for GatewayClient."""
import pytest
import httpx
import click

from mobiclaw.cli.http_client import GatewayClient


def _health_handler(request: httpx.Request) -> httpx.Response:
    if "health" in str(request.url):
        return httpx.Response(200, json={"status": "ok"})
    return httpx.Response(404)


@pytest.mark.asyncio
async def test_health_returns_ok():
    transport = httpx.MockTransport(_health_handler)
    client = GatewayClient("http://test", transport=transport)
    result = await client.health()
    assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_includes_auth_header_when_api_key_set():
    received_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received_headers["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)
    client = GatewayClient("http://test", api_key="secret", transport=transport)
    await client.health()
    assert received_headers["authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_submit_task():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/v1/task" in str(request.url) and request.method == "POST":
            return httpx.Response(200, json={"job_id": "job-1", "status": "pending"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = GatewayClient("http://test", transport=transport)
    result = await client.submit_task(mode="chat", messages=[])
    assert result["job_id"] == "job-1"
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_get_job():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/v1/jobs/job-1" in str(request.url):
            return httpx.Response(
                200,
                json={"job_id": "job-1", "status": "completed", "result": "done"},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = GatewayClient("http://test", transport=transport)
    result = await client.get_job("job-1")
    assert result["job_id"] == "job-1"
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_health_raises_click_exception_on_http_error():
    transport = httpx.MockTransport(
        lambda r: httpx.Response(500, text="Internal Server Error")
    )
    client = GatewayClient("http://test", transport=transport)
    with pytest.raises(click.ClickException) as exc_info:
        await client.health()
    assert "HTTP 500" in str(exc_info.value)
    assert "Internal Server Error" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_files(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/v1/chat/files" in str(request.url) and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {"name": "a.txt", "path": "/uploads/a.txt", "size": 5},
                    ]
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = GatewayClient("http://test", transport=transport)
    with open(tmp_path / "a.txt", "wb") as f:
        f.write(b"hello")
    result = await client.upload_files([str(tmp_path / "a.txt")])
    assert "files" in result
    assert len(result["files"]) == 1
    assert result["files"][0]["path"] == "/uploads/a.txt"


@pytest.mark.asyncio
async def test_health_raises_click_exception_on_connect_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    transport = httpx.MockTransport(handler)
    client = GatewayClient("http://test", transport=transport)
    with pytest.raises(click.ClickException) as exc_info:
        await client.health()
    assert "Cannot connect to gateway server" in str(exc_info.value)
