"""Async calls to the backend's own API, used only by the maintenance program.

A BackendAdmin a saját FastAPI backendünket hívja (nem a Rendert): a
tokennel védett belső végpontokon zárolja/feloldja az írásokat és
próba-írást végez, a nyilvános olvasó végpontokon pedig tartalmi
pillanatképet készít. Az ingyenes Render backend elalhat, és a
felébredése ~1 perc, ezért a hívások korlátozottan újrapróbálkoznak.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from maintenance.retry import Sleep, with_retries
from maintenance.snapshot import Snapshot, fetch_snapshot

T = TypeVar("T")


class BackendAdmin:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: float = 60.0,
        attempts: int = 5,
        base_delay_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-Maintenance-Token": token},
            timeout=timeout_seconds,
            transport=transport,
        )
        self._attempts = attempts
        self._base_delay = base_delay_seconds
        self._sleep = sleep

    async def __aenter__(self) -> BackendAdmin:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._client.aclose()

    async def _retrying(self, description: str, operation: Callable[[], Awaitable[T]]) -> T:
        # Csak hálózati hibát és 5xx-et próbálunk újra; 4xx (pl. rossz
        # token) azonnal továbbmegy a hívóhoz.
        async def attempt() -> T:
            try:
                return await operation()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    raise httpx.TransportError(str(exc)) from exc
                raise

        return await with_retries(
            attempt,
            retry_on=(httpx.TransportError,),
            attempts=self._attempts,
            base_delay_seconds=self._base_delay,
            description=f"backend {description}",
            sleep=self._sleep,
        )

    async def set_writes_frozen(self, frozen: bool) -> bool:
        """POST /internal/maintenance-mode; a backend által visszaigazolt állapotot adja."""

        async def call() -> bool:
            resp = await self._client.post("/internal/maintenance-mode", json={"enabled": frozen})
            resp.raise_for_status()
            return resp.json()["writes_frozen"]

        return await self._retrying("set maintenance mode", call)

    async def fetch_snapshot(self) -> Snapshot:
        """A teljes tartalmi pillanatkép a nyilvános olvasó végpontokon át."""
        return await self._retrying("snapshot", lambda: fetch_snapshot(self._client))

    async def verify_writable(self) -> bool:
        """Egy kontrollált próba-írás a tokenes belső végponton, miközben a
        normál alkalmazás-írások zárolva maradnak."""

        async def call() -> bool:
            resp = await self._client.post("/internal/write-check")
            resp.raise_for_status()
            return resp.json()["writable"]

        return await self._retrying("write check", call)
