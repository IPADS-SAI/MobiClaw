"""Gateway HTTP client."""
from __future__ import annotations

import mimetypes
from io import BytesIO
from pathlib import Path
from typing import Any

import click
import httpx

try:
    from rich.progress import Progress, SpinnerColumn, TextColumn
except ImportError:
    Progress = None


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

    async def download_file(
        self, job_id: str, file_name: str, output_path: Path
    ) -> None:
        """Stream GET /api/v1/files/{job_id}/{file_name} to output_path with progress bar."""
        url = f"{self.base_url}/api/v1/files/{job_id}/{file_name}"
        async with self._client() as client:
            try:
                async with client.stream(
                    "GET", url, headers=self._headers()
                ) as response:
                    response.raise_for_status()
                    total = response.headers.get("content-length")
                    total_int = int(total) if total else None

                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        if Progress is not None and total_int is not None:
                            with Progress(
                                SpinnerColumn(),
                                TextColumn("[progress.description]{task.description}"),
                                *Progress.get_default_columns(),
                                transient=True,
                            ) as progress:
                                task = progress.add_task(
                                    f"Downloading {file_name}",
                                    total=total_int,
                                )
                                async for chunk in response.aiter_bytes():
                                    f.write(chunk)
                                    progress.update(task, advance=len(chunk))
                        else:
                            async for chunk in response.aiter_bytes():
                                f.write(chunk)
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

    async def list_devices(self) -> dict[str, Any]:
        """GET /api/v1/devices. Returns {devices: [...], count: N}."""
        async with self._client() as client:
            try:
                r = await client.get(
                    f"{self.base_url}/api/v1/devices",
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

    async def get_device(self, device_id: str) -> dict[str, Any]:
        """GET /api/v1/devices/{id}. Returns device record."""
        async with self._client() as client:
            try:
                r = await client.get(
                    f"{self.base_url}/api/v1/devices/{device_id}",
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

    async def device_heartbeat(
        self,
        device_id: str,
        tailscale_ip: str | None = None,
        adb_port: int | None = None,
        device_name: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/devices/heartbeat. Returns {status, device_id, timestamp}."""
        body: dict[str, Any] = {"device_id": device_id}
        if tailscale_ip is not None:
            body["tailscale_ip"] = tailscale_ip
        if adb_port is not None:
            body["adb_port"] = adb_port
        if device_name is not None:
            body["device_name"] = device_name
        async with self._client() as client:
            try:
                r = await client.post(
                    f"{self.base_url}/api/v1/devices/heartbeat",
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

    async def remove_device(self, device_id: str) -> dict[str, Any]:
        """DELETE /api/v1/devices/{id}. Returns JSON."""
        async with self._client() as client:
            try:
                r = await client.delete(
                    f"{self.base_url}/api/v1/devices/{device_id}",
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

    async def get_env(self) -> dict[str, Any]:
        """GET /api/v1/env. Returns {path, content, variables}."""
        async with self._client() as client:
            try:
                r = await client.get(
                    f"{self.base_url}/api/v1/env",
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

    async def get_env_schema(self) -> dict[str, Any]:
        """GET /api/v1/env/schema. Returns {path, schema, values, unmanaged, variables, content}."""
        async with self._client() as client:
            try:
                r = await client.get(
                    f"{self.base_url}/api/v1/env/schema",
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

    async def set_env_content(self, content: str) -> dict[str, Any]:
        """PUT /api/v1/env with body {content}."""
        async with self._client() as client:
            try:
                r = await client.put(
                    f"{self.base_url}/api/v1/env",
                    json={"content": content},
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

    async def set_env_structured(
        self,
        values: dict[str, str],
        unmanaged: dict[str, str] | None = None,
        preserve_unmanaged: bool = True,
    ) -> dict[str, Any]:
        """PUT /api/v1/env/schema with body {values, unmanaged, preserve_unmanaged}."""
        body: dict[str, Any] = {
            "values": values,
            "preserve_unmanaged": preserve_unmanaged,
        }
        if unmanaged is not None:
            body["unmanaged"] = unmanaged
        async with self._client() as client:
            try:
                r = await client.put(
                    f"{self.base_url}/api/v1/env/schema",
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

    async def send_feishu_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/feishu/events with JSON body."""
        async with self._client() as client:
            try:
                r = await client.post(
                    f"{self.base_url}/api/v1/feishu/events",
                    json=payload,
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
