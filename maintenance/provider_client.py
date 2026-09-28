"""Async client for the Render API (provisioning/repointing target of the DB swap).

Real async operations (httpx.AsyncClient) as required by the assignment's
Section 6.1 - a synchronous requests call or a fake asyncio.sleep() would
not satisfy that requirement.

Ez a fájl az egyetlen hely, ami ténylegesen a Render felhő API-jával
beszél (HTTP-n keresztül, aszinkron módon). A maintenance/decision.py
és a maintenance/db_swap_controller.py ezen az osztályon keresztül éri
el a Rendert — sosem közvetlenül httpx-hívásokkal szórva a kódban.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from maintenance.config import RENDER_API_BASE_URL, RENDER_API_KEY

logger = logging.getLogger(__name__)


class RenderApiError(RuntimeError):
    """Base class for all Render API failures."""


class TransientRenderError(RenderApiError):
    """A retryable failure: network error, timeout, or 5xx response.

    Ezekre a hívó (db_swap_controller) korlátozott számú újrapróbálkozást
    végezhet — de sosem korlátlanul (Section 6.4: "bounded retries")."""


class PermissionOrQuotaError(RenderApiError):
    """A non-retryable failure: 401/403/429 - never auto-escalate billing.

    Jogosultsági/kvóta-hiba esetén a program SOSEM léphet automatikusan
    fizetős csomagra vagy próbálkozhat végtelenül — ilyenkor a helyes
    válasz a biztonságos leállás és a hiba naplózása."""


def _headers() -> dict[str, str]:
    """A Render API minden hívásához szükséges azonosító fejlécet
    állítja össze — az API kulcsot a maintenance/config.py adja
    (végső soron a RENDER_API_KEY környezeti változóból, sosem
    hardkódolva)."""
    return {"Authorization": f"Bearer {RENDER_API_KEY}", "Content-Type": "application/json"}


def _raise_for_status(response: httpx.Response) -> None:
    """A nyers HTTP státuszkódot a fenti kivétel-típusok egyikévé
    alakítja, hogy a hívó kód (retry-logika) tudja megkülönböztetni az
    "érdemes újrapróbálni" és a "sosem próbáld újra" eseteket."""
    if response.status_code in (401, 403, 429):
        raise PermissionOrQuotaError(f"{response.status_code}: {response.text}")
    if response.status_code >= 500:
        raise TransientRenderError(f"{response.status_code}: {response.text}")
    response.raise_for_status()


class RenderClient:
    """Thin async wrapper around the subset of the Render API the swap needs."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        # Ha a hívó ad át saját httpx.AsyncClient-et (pl. teszteléshez,
        # mock szerverrel), azt használjuk; egyébként létrehozunk egy
        # sajátot, amit majd nekünk is kell lezárnunk (`_owns_client`).
        self._client = client or httpx.AsyncClient(base_url=RENDER_API_BASE_URL, headers=_headers())
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_postgres_instance(self, instance_id: str) -> dict:
        """Lekérdezi egy adott Render Postgres-instance jelenlegi
        állapotát (pl. fut-e, mikor jár le) — ezt hívja a maintenance
        ütemezett ellenőrzése (Section 6.1)."""
        resp = await self._client.get(f"/postgres/{instance_id}")
        _raise_for_status(resp)
        return resp.json()

    async def create_postgres_instance(
        self, name: str, plan: str, region: str, postgres_version: str, owner_id: str
    ) -> dict:
        """Új Postgres-instance létrehozása a Render API-n keresztül —
        a csere 3. lépése (provisioning). A paraméterek (plan/region/
        version) explicit meg vannak adva, nem "alapértelmezésre bízva"
        (Section 6.3 elvárása)."""
        payload = {
            "name": name,
            "plan": plan,
            "region": region,
            "version": postgres_version,
            "ownerId": owner_id,
        }
        resp = await self._client.post("/postgres", json=payload)
        _raise_for_status(resp)
        return resp.json()

    async def delete_postgres_instance(self, instance_id: str) -> None:
        """A régi instance törlése — csak akkor hívjuk, ha a Render
        fiók csak egy aktív instance-t enged (free tier), és a csere
        ellenőrzötten sikeres volt."""
        resp = await self._client.delete(f"/postgres/{instance_id}")
        _raise_for_status(resp)

    async def wait_until_available(
        self, instance_id: str, timeout_seconds: float = 300, poll_interval_seconds: float = 5
    ) -> dict:
        """VALÓDI aszinkron várakozás: `await asyncio.sleep(...)` a
        pollozási ciklusok között, miközben más async feladatok is
        futhatnának eközben — ez teljesíti a Section 6.1 "valódi async"
        követelményét (nem egy szimulált, mindent blokkoló sleep).
        Időtúllépéskor (timeout_seconds után) feladja és hibát dob,
        sosem vár a végtelenségig."""
        elapsed = 0.0
        while elapsed < timeout_seconds:
            instance = await self.get_postgres_instance(instance_id)
            if instance.get("status") == "available":
                return instance
            await asyncio.sleep(poll_interval_seconds)
            elapsed += poll_interval_seconds
        raise TimeoutError(f"Postgres instance {instance_id} did not become available in time")

    async def update_service_env_var(self, service_id: str, key: str, value: str) -> None:
        """A backend Render web service egy környezeti változóját (pl.
        DATABASE_URL) frissíti — ez a csere 5. lépése (repointing).
        Előbb lekéri az ÖSSZES jelenlegi env-változót, hogy a frissítés
        ne törölje ki a többit, csak a keresett kulcsot cserélje/adja
        hozzá."""
        env_vars_resp = await self._client.get(f"/services/{service_id}/env-vars")
        _raise_for_status(env_vars_resp)
        current = env_vars_resp.json()

        updated = [{"key": item["envVar"]["key"], "value": item["envVar"]["value"]} for item in current]
        found = False
        for item in updated:
            if item["key"] == key:
                item["value"] = value
                found = True
        if not found:
            updated.append({"key": key, "value": value})

        resp = await self._client.put(f"/services/{service_id}/env-vars", json=updated)
        _raise_for_status(resp)

    async def trigger_deploy(self, service_id: str) -> dict:
        """Új deploy-t indít a backend service-en, hogy az imént
        frissített DATABASE_URL ténylegesen érvénybe lépjen a futó
        alkalmazásban."""
        resp = await self._client.post(f"/services/{service_id}/deploys", json={})
        _raise_for_status(resp)
        return resp.json()

    async def wait_until_deploy_live(
        self,
        service_id: str,
        deploy_id: str,
        timeout_seconds: float = 600,
        poll_interval_seconds: float = 10,
    ) -> dict:
        """Kivárja (aszinkron pollozással), amíg a deploy "live"
        állapotba kerül. Ha a deploy kifejezetten hibás állapotba
        kerül (build_failed/update_failed/canceled), azonnal hibát
        dob — nem vár feleslegesen tovább egy már elbukott deployra."""
        elapsed = 0.0
        while elapsed < timeout_seconds:
            resp = await self._client.get(f"/services/{service_id}/deploys/{deploy_id}")
            _raise_for_status(resp)
            deploy = resp.json()
            if deploy.get("status") == "live":
                return deploy
            if deploy.get("status") in ("build_failed", "update_failed", "canceled"):
                raise RenderApiError(f"Deploy {deploy_id} failed with status {deploy.get('status')}")
            await asyncio.sleep(poll_interval_seconds)
            elapsed += poll_interval_seconds
        raise TimeoutError(f"Deploy {deploy_id} did not go live in time")
