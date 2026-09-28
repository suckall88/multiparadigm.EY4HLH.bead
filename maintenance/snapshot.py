"""Content snapshots used to prove a restore reproduced the data exactly.

Section 6.2: a visszaállítás után nem elég a rekordszám — egyeznie kell
a rekordok tartalmának, a kapcsolatoknak és a témaspecifikus szabály
eredményének is. Ezért a csere előtt a backend saját olvasó végpontjain
keresztül "pillanatképet" készítünk (gyakorlatok, edzések a szettjeikkel,
és gyakorlatonként a progresszió-elemzés eredménye), majd a
visszaállított adatbázison ugyanezt lekérve összehasonlítjuk a kettőt.

A progresszió-szabály referencia-dátuma az utolsó rögzített edzésnap
(nem a "ma"), így ugyanarra az adatra mindig ugyanazt adja — a két
pillanatkép időben eltolva is összevethető.
"""

from __future__ import annotations

from typing import Any

import httpx

Snapshot = dict[str, Any]


async def fetch_snapshot(client: httpx.AsyncClient) -> Snapshot:
    """Read the full app state through the backend's public read endpoints.

    A `client` base_url-je a backendre mutat (élesben a Render backend,
    a próbavisszaállításnál egy memóriában futó példány)."""
    exercises_resp = await client.get("/exercises")
    exercises_resp.raise_for_status()
    exercises = exercises_resp.json()

    sessions_resp = await client.get("/sessions")
    sessions_resp.raise_for_status()

    progression: dict[str, Any] = {}
    for exercise in exercises:
        resp = await client.get(f"/exercises/{exercise['id']}/progression")
        resp.raise_for_status()
        progression[str(exercise["id"])] = resp.json()

    return {"exercises": exercises, "sessions": sessions_resp.json(), "progression": progression}


def compare_snapshots(expected: Snapshot, actual: Snapshot) -> list[str]:
    """Pure comparison: returns human-readable differences (empty list = identical).

    Funkcionális segédfüggvény: nincs mellékhatása, csak a két bemenetet
    veti össze részenként, hogy a naplóban látszódjon, MI tért el."""
    differences: list[str] = []
    for section in ("exercises", "sessions", "progression"):
        want = expected.get(section)
        got = actual.get(section)
        if want != got:
            differences.extend(_section_differences(section, want, got))
    return differences


def _section_differences(section: str, want: Any, got: Any) -> list[str]:
    """Egy eltérő szakasz (lista vagy gyakorlatonkénti dict) részletezése."""
    if isinstance(want, list) and isinstance(got, list):
        want_by_id = {item["id"]: item for item in want}
        got_by_id = {item["id"]: item for item in got}
        found = [f"{section}: record id={i} missing" for i in sorted(set(want_by_id) - set(got_by_id))]
        found += [f"{section}: unexpected record id={i}" for i in sorted(set(got_by_id) - set(want_by_id))]
        found += [
            f"{section}: record id={i} differs"
            for i in sorted(set(want_by_id) & set(got_by_id))
            if want_by_id[i] != got_by_id[i]
        ]
        return found or [f"{section}: record order differs"]
    if isinstance(want, dict) and isinstance(got, dict):
        return [
            f"{section}: exercise {key} result differs"
            for key in sorted(set(want) | set(got))
            if want.get(key) != got.get(key)
        ]
    return [f"{section}: missing or malformed"]
