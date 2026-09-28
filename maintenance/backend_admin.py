"""Async calls to the backend's own API, used only by the maintenance program.

Ez a fájl köti össze a maintenance vezérlőprogramot a saját FastAPI
backendünkkel (nem a Renderrel!) — a backend/routers/internal.py
tokennel védett végpontjait hívja meg, hogy zárolja/feloldja az
írásokat, és ellenőrizze a cserét.
"""

from __future__ import annotations

import httpx

from maintenance.config import BACKEND_URL, MAINTENANCE_TOKEN


async def set_maintenance_mode(client: httpx.AsyncClient, enabled: bool) -> bool:
    """A csere 2. lépése: POST /internal/maintenance-mode meghívása,
    hogy zárolja (enabled=True) vagy feloldja (enabled=False) az
    írásokat a backend/maintenance_mode.py-ban. A visszakapott
    `writes_frozen` mezővel a hívó (db_swap_controller) meg tudja
    erősíteni, hogy a zárolás ténylegesen érvénybe lépett."""
    resp = await client.post(
        f"{BACKEND_URL}/internal/maintenance-mode",
        json={"enabled": enabled},
        headers={"X-Maintenance-Token": MAINTENANCE_TOKEN},
    )
    resp.raise_for_status()
    return resp.json()["writes_frozen"]


async def verify_restored_content(client: httpx.AsyncClient) -> bool:
    """A plain healthy-status check is not enough; confirm a real read endpoint responds.

    A csere 5. lépésének ellenőrzése: nem elég, ha a /health "ok"-ot
    mond — ténylegesen le kell tudni kérdezni valós adatot (itt a
    GET /exercises-t) az ÚJ, restore-olt adatbázisból, ami bizonyítja,
    hogy a visszaállított tartalom valóban elérhető a backendről."""
    resp = await client.get(f"{BACKEND_URL}/exercises")
    resp.raise_for_status()
    return isinstance(resp.json(), list)


async def verify_writable(client: httpx.AsyncClient) -> bool:
    """One controlled test write via the token-authorized internal check,
    performed while normal application writes stay blocked.

    Meghívja a backend/routers/internal.py::write_check belső
    végpontját, ami egy próba-sort ír be, majd rögtön törli — ezzel
    igazolva, hogy az új adatbázis írható, miközben a normál
    felhasználói írások továbbra is zárolva vannak."""
    resp = await client.post(
        f"{BACKEND_URL}/internal/write-check",
        headers={"X-Maintenance-Token": MAINTENANCE_TOKEN},
    )
    resp.raise_for_status()
    return resp.json()["writable"]
