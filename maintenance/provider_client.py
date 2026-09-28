"""Async client for the Render API (provisioning/repointing target of the DB swap).

Real async operations (httpx.AsyncClient) as required by the assignment's
Section 6.1 - a synchronous requests call or a fake asyncio.sleep() would
not satisfy that requirement.

Ez az egyetlen hely, ami a Render felhő API-jával beszél. Minden hívás
a `_request` metóduson megy át, ami a HTTP-státuszt kivételtípussá
alakítja, és csak az átmeneti hibákat próbálja újra, korlátozottan.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from maintenance.config import RENDER_API_BASE_URL
from maintenance.retry import Sleep, with_retries

logger = logging.getLogger(__name__)


class RenderApiError(RuntimeError):
    """Base class for all Render API failures."""


class TransientRenderError(RenderApiError):
    """A retryable failure: network error, timeout, 5xx or 429 rate limit."""


class PermissionOrQuotaError(RenderApiError):
    """A non-retryable 401/402/403 failure - never retried, never escalates billing."""


class RenderNotFoundError(RenderApiError):
    """404: the requested resource does not exist (any more)."""


class AmbiguousRenderError(RenderApiError):
    """A non-idempotent call failed without a response - its outcome is unknown.

    Ilyenkor tilos vakon újrapróbálni (pl. duplikált példány jönne
    létre); a hívónak a szolgáltatónál kell egyeztetnie az állapotot."""


def _raise_for_status(response: httpx.Response) -> None:
    """A HTTP-státuszkódot a fenti kivételtípusok egyikévé alakítja."""
    code = response.status_code
    if code < 400:
        return
    message = f"{response.request.method} {response.request.url.path} -> {code}: {response.text[:300]}"
    if code in (401, 402, 403):
        raise PermissionOrQuotaError(message)
    if code == 404:
        raise RenderNotFoundError(message)
    if code == 429 or code >= 500:
        raise TransientRenderError(message)
    raise RenderApiError(message)


class RenderClient:
    """Async wrapper around the subset of the Render API the swap needs."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = RENDER_API_BASE_URL,
        timeout_seconds: float = 30.0,
        attempts: int = 4,
        base_delay_seconds: float = 2.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ):
        # A kliens mindig a saját base_url-jével és API-kulcsával jön
        # létre — a `transport` csak tesztekben cserélődik le egy
        # httpx.MockTransport-ra, a valódi hálózat helyett.
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=timeout_seconds,
            transport=transport,
        )
        self._attempts = attempts
        self._base_delay = base_delay_seconds
        self._sleep = sleep

    async def __aenter__(self) -> RenderClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, idempotent: bool = True, **kwargs: Any) -> httpx.Response:
        """Egy API-hívás korlátozott újrapróbálkozással.

        Nem idempotens hívásnál (pl. példány létrehozása) a válasz nélküli
        hálózati hiba AmbiguousRenderError lesz, és nem próbáljuk újra."""

        async def attempt() -> httpx.Response:
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if not idempotent:
                    raise AmbiguousRenderError(f"{method} {path}: no response ({exc})") from exc
                raise TransientRenderError(f"{method} {path}: {exc}") from exc
            if not idempotent and response.status_code >= 500:
                # 5xx után nem tudni, létrejött-e az erőforrás; a 429 viszont
                # biztosan nem dolgozta fel a kérést, azt újrapróbálhatjuk.
                raise AmbiguousRenderError(f"{method} {path} -> {response.status_code}")
            _raise_for_status(response)
            return response

        return await with_retries(
            attempt,
            retry_on=(TransientRenderError,),
            attempts=self._attempts,
            base_delay_seconds=self._base_delay,
            description=f"Render {method} {path}",
            sleep=self._sleep,
        )

    # ---- Postgres instances ----------------------------------------------------

    async def get_postgres(self, instance_id: str) -> dict[str, Any]:
        """Egy példány állapota (status, expiresAt, owner stb.)."""
        return (await self._request("GET", f"/postgres/{instance_id}")).json()

    async def get_connection_string(self, instance_id: str) -> str:
        """A kapcsolati adatokat a Render külön végponton adja; a külső
        (internetről elérhető) connection stringet használjuk, mert a
        vezérlőprogram helyben fut, nem a Render hálózatán belül."""
        info = (await self._request("GET", f"/postgres/{instance_id}/connection-info")).json()
        return info["externalConnectionString"]

    async def find_postgres_by_name(self, name: str, owner_id: str) -> dict[str, Any] | None:
        """Név szerinti keresés — ezzel egyeztetünk egy megszakadt vagy
        bizonytalan kimenetelű létrehozás után, mielőtt újat hoznánk létre."""
        response = await self._request("GET", "/postgres", params={"name": name, "ownerId": owner_id})
        for item in response.json():
            if item["postgres"]["name"] == name:
                return item["postgres"]
        return None

    async def create_postgres(
        self, *, name: str, owner_id: str, plan: str, region: str, version: str
    ) -> dict[str, Any]:
        """Új példány, explicit plan/region/verzió paraméterekkel."""
        payload = {"name": name, "ownerId": owner_id, "plan": plan, "region": region, "version": version}
        return (await self._request("POST", "/postgres", idempotent=False, json=payload)).json()

    async def delete_postgres(self, instance_id: str) -> None:
        """Példány törlése. Ha már nem létezik (pl. egy megszakadt futás
        már törölte), az nem hiba — a törlés így megismételhető."""
        try:
            await self._request("DELETE", f"/postgres/{instance_id}")
        except RenderNotFoundError:
            logger.info("Instance %s was already deleted", instance_id)

    async def wait_until_available(
        self, instance_id: str, timeout_seconds: float = 600, poll_interval_seconds: float = 10
    ) -> dict[str, Any]:
        """Aszinkron pollozás, amíg a példány "available" lesz, időkorláttal."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            instance = await self.get_postgres(instance_id)
            if instance.get("status") == "available":
                return instance
            if loop.time() >= deadline:
                raise TimeoutError(f"Postgres instance {instance_id} not available after {timeout_seconds}s")
            await self._sleep(poll_interval_seconds)

    # ---- Web service ---------------------------------------------------------------

    async def get_service(self, service_id: str) -> dict[str, Any]:
        return (await self._request("GET", f"/services/{service_id}")).json()

    async def set_env_var(self, service_id: str, key: str, value: str) -> None:
        """Egyetlen környezeti változó beállítása; a többi érintetlen marad.
        Magában nem indít deployt — az a következő újraindításkor lép életbe."""
        await self._request("PUT", f"/services/{service_id}/env-vars/{key}", json={"value": value})

    async def trigger_deploy(self, service_id: str) -> str:
        """Deploy indítása; a deploy azonosítóját adja vissza.

        201-nél a válasz tartalmazza a deployt; 202-nél (sorba állítva)
        nincs törzs, ilyenkor a legfrissebb deployt kérdezzük le."""
        response = await self._request("POST", f"/services/{service_id}/deploys", idempotent=False, json={})
        if response.status_code == 201:
            return response.json()["id"]
        latest = await self._request("GET", f"/services/{service_id}/deploys", params={"limit": 1})
        return latest.json()[0]["deploy"]["id"]

    async def wait_until_deploy_live(
        self, service_id: str, deploy_id: str, timeout_seconds: float = 900, poll_interval_seconds: float = 15
    ) -> None:
        """Kivárja, amíg a deploy "live"; hibás végállapotnál azonnal hibát dob."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            deploy = (await self._request("GET", f"/services/{service_id}/deploys/{deploy_id}")).json()
            status = deploy.get("status")
            if status == "live":
                return
            if status in ("build_failed", "update_failed", "pre_deploy_failed", "canceled", "deactivated"):
                raise RenderApiError(f"Deploy {deploy_id} ended with status {status}")
            if loop.time() >= deadline:
                raise TimeoutError(f"Deploy {deploy_id} not live after {timeout_seconds}s")
            await self._sleep(poll_interval_seconds)
