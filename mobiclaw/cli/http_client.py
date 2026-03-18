"""Gateway HTTP client."""
from __future__ import annotations

import mimetypes
from io import BytesIO
from pathlib import Path
from typing import Any

import click
import httpx


class GatewayClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _client(self) -> httpx.AsyncClient:
        if self._transport is not None:
            return httpx.AsyncClient(transport=self._transport)
        return httpx.AsyncClient()

    async def health(self) -> dict[str, Any]:
        async with self._client() as client:
            try:
                r = await client.get(
                    f"{self.base_url}/health",
                    headers=self._headers(),
                )
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise click.ClickException(
                    f"HTTP {e.response.status_code}: {e.response.text}"
                )
            except httpx.ConnectError:
                raise click.ClickException(
                    "Cannot connect to gateway server, check server_url"
                )

    async def submit_task(self, **kwargs: Any) -> dict[str, Any]:
        async with self._client() as client:
            try:
                r = await client.post(
                    f"{self.base_url}/api/v1/task",
                    json=kwargs,
                    headers=self._headers(),
                )
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise click.ClickException(
                    f"HTTP {e.response.status_code}: {e.response.text}"
                )
            except httpx.ConnectError:
                raise click.ClickException(
                    "Cannot connect to gateway server, check server_url"
                )

    async def get_job(self, job_id: str) -> dict[str, Any]:
        async with self._client() as client:
            try:
                r = await client.get(
                    f"{self.base_url}/api/v1/jobs/{job_id}",
                    headers=self._headers(),
                )
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise click.ClickException(
                    f"HTTP {e.response.status_code}: {e.response.text}"
                )
            except httpx.ConnectError:
                raise click.ClickException(
                    "Cannot connect to gateway server, check server_url"
                )

    async def list_schedules(self) -> dict[str, Any]:
        """GET /api/v1/schedules. Returns JSON with schedules list."""
        async with self._client() as client:
            try:
                r = await client.get(
                    f"{self.base_url}/api/v1/schedules",
                    headers=self._headers(),
                )
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise click.ClickException(
                    f"HTTP {e.response.status_code}: {e.response.text}"
                )
            except httpx.ConnectError:
                raise click.ClickException(
                    "Cannot connect to gateway server, check server_url"
                )

    async def cancel_schedule(self, schedule_id: str) -> dict[str, Any]:
        """DELETE /api/v1/schedules/{id}. Returns JSON."""
        async with self._client() as client:
            try:
                r = await client.delete(
                    f"{self.base_url}/api/v1/schedules/{schedule_id}",
                    headers=self._headers(),
                )
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise click.ClickException(
                    f"HTTP {e.response.status_code}: {e.response.text}"
                )
            except httpx.ConnectError:
                raise click.ClickException(
                    "Cannot connect to gateway server, check server_url"
                )

    async def list_mcp_servers(self) -> dict[str, Any]:
        """GET /api/v1/mcp/servers. Returns {servers, enabled}."""
        async with self._client() as client:
            try:
                r = await client.get(
                    f"{self.base_url}/api/v1/mcp/servers",
                    headers=self._headers(),
                )
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise click.ClickException(
                    f"HTTP {e.response.status_code}: {e.response.text}"
                )
            except httpx.ConnectError:
                raise click.ClickException(
                    "Cannot connect to gateway server, check server_url"
                )

    async def add_mcp_server(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/mcp/servers. Returns {ok, name, status}."""
        async with self._client() as client:
            try:
                r = await client.post(
                    f"{self.base_url}/api/v1/mcp/servers",
                    json=body,
                    headers=self._headers(),
                )
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise click.ClickException(
                    f"HTTP {e.response.status_code}: {e.response.text}"
                )
            except httpx.ConnectError:
                raise click.ClickException(
                    "Cannot connect to gateway server, check server_url"
                )

    async def remove_mcp_server(self, name: str) -> dict[str, Any]:
        """DELETE /api/v1/mcp/servers/{name}. Returns {ok, name, status}."""
        async with self._client() as client:
            try:
                r = await client.delete(
                    f"{self.base_url}/api/v1/mcp/servers/{name}",
                    headers=self._headers(),
                )
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise click.ClickException(
                    f"HTTP {e.response.status_code}: {e.response.text}"
                )
            except httpx.ConnectError:
                raise click.ClickException(
                    "Cannot connect to gateway server, check server_url"
                )

    async def upload_files(self, file_paths: list[str]) -> dict[str, Any]:
        """POST /api/v1/chat/files with multipart/form-data. Returns JSON with files list."""
        if not file_paths:
            return {"files": []}
        files_to_send: list[tuple[str, tuple[str, BytesIO, str | None]]] = []
        for path in file_paths:
            p = Path(path)
            if not p.exists():
                raise click.ClickException(f"File not found: {path}")
            content = p.read_bytes()
            name = p.name
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            files_to_send.append(("files", (name, BytesIO(content), mime)))
        async with self._client() as client:
            try:
                r = await client.post(
                    f"{self.base_url}/api/v1/chat/files",
                    headers=self._headers(),
                    files=files_to_send,
                )
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise click.ClickException(
                    f"HTTP {e.response.status_code}: {e.response.text}"
                )
            except httpx.ConnectError:
                raise click.ClickException(
                    "Cannot connect to gateway server, check server_url"
                )

    async def list_sessions(self) -> dict[str, Any]:
        """GET /api/v1/chat/sessions. Returns {sessions: [...]}."""
        async with self._client() as client:
            try:
                r = await client.get(
                    f"{self.base_url}/api/v1/chat/sessions",
                    headers=self._headers(),
                )
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise click.ClickException(
                    f"HTTP {e.response.status_code}: {e.response.text}"
                )
            except httpx.ConnectError:
                raise click.ClickException(
                    "Cannot connect to gateway server, check server_url"
                )

    async def get_session(self, context_id: str, limit: int = 20) -> dict[str, Any]:
        """GET /api/v1/chat/sessions/{id}?limit=. Returns session with messages."""
        async with self._client() as client:
            try:
                r = await client.get(
                    f"{self.base_url}/api/v1/chat/sessions/{context_id}",
                    params={"limit": limit},
                    headers=self._headers(),
                )
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise click.ClickException(
                    f"HTTP {e.response.status_code}: {e.response.text}"
                )
            except httpx.ConnectError:
                raise click.ClickException(
                    "Cannot connect to gateway server, check server_url"
                )

    async def delete_session(self, context_id: str) -> dict[str, Any]:
        """DELETE /api/v1/chat/sessions/{id}."""
        async with self._client() as client:
            try:
                r = await client.delete(
                    f"{self.base_url}/api/v1/chat/sessions/{context_id}",
                    headers=self._headers(),
                )
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise click.ClickException(
                    f"HTTP {e.response.status_code}: {e.response.text}"
                )
            except httpx.ConnectError:
                raise click.ClickException(
                    "Cannot connect to gateway server, check server_url"
                )
