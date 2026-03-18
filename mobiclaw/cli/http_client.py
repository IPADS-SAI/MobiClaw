"""Gateway HTTP client."""
from __future__ import annotations

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
