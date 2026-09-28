"""Thin HTTP client wrapping the backend's own REST API.

The frontend never talks to the database directly - every data operation
goes through these functions, which call FastAPI endpoints.

FONTOS: a Streamlit main scriptje (frontend/app.py) és az aloldalak
(frontend/pages/*.py) csak a saját mappájukat látják a sys.path-on,
ezért ezt a modult mindig `from api_client import ...` formában kell
importálni (nem `from frontend.api_client import ...`) — ez egy valós
hibaként derült ki az építés közben.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from config import BACKEND_URL

_TIMEOUT = 5.0


def _get(path: str, params: dict[str, Any] | None = None) -> httpx.Response:
    """Közös GET-segédfüggvény: a BACKEND_URL-hez fűzi a végpont útját,
    időtúllépéssel (5 mp) hívja meg a backendet."""
    return httpx.get(f"{BACKEND_URL}{path}", params=params, timeout=_TIMEOUT)


def _post(path: str, json: dict[str, Any]) -> httpx.Response:
    """Közös POST-segédfüggvény, ugyanazzal az időtúllépési logikával."""
    return httpx.post(f"{BACKEND_URL}{path}", json=json, timeout=_TIMEOUT)


def list_exercises(category: str | None = None) -> list[dict[str, Any]]:
    """GET /exercises hívása. `raise_for_status()` HTTP-hibakódnál
    (4xx/5xx) kivételt dob — ezt az oldalak (frontend/pages/*.py)
    fogják el `httpx.HTTPError`-ként, és jelenítik meg felhasználóbarát
    hibaüzenetként."""
    params = {"category": category} if category else None
    resp = _get("/exercises", params=params)
    resp.raise_for_status()
    return resp.json()


def create_exercise(name: str, category: str) -> dict[str, Any]:
    """POST /exercises: új gyakorlat mentése — a visszakapott JSON-t
    (dict) adja tovább a hívó Streamlit-oldalnak."""
    resp = _post("/exercises", {"name": name, "category": category})
    resp.raise_for_status()
    return resp.json()


def create_session(session_date: date, notes: str | None, sets: list[dict[str, Any]]) -> dict[str, Any]:
    """POST /sessions: egy teljes edzésalkalom (dátum + megjegyzés +
    sorozatok) elküldése egyetlen kérésben. A `date` objektumot
    `.isoformat()`-tal szöveggé kell alakítani, mert a JSON nem ismeri
    közvetlenül a Python date típust."""
    resp = _post(
        "/sessions",
        {"session_date": session_date.isoformat(), "notes": notes, "sets": sets},
    )
    resp.raise_for_status()
    return resp.json()


def list_sessions(start_date: date | None = None, end_date: date | None = None) -> list[dict[str, Any]]:
    """GET /sessions: edzésalkalmak listázása, opcionális dátumszűréssel."""
    params: dict[str, Any] = {}
    if start_date:
        params["start_date"] = start_date.isoformat()
    if end_date:
        params["end_date"] = end_date.isoformat()
    resp = _get("/sessions", params=params or None)
    resp.raise_for_status()
    return resp.json()


def get_progression(exercise_id: int, lookback_weeks: int = 8) -> dict[str, Any]:
    """GET /exercises/{id}/progression: a fejlődés-elemzés eredménye
    (pontok, trend, státusz) — ezt rajzolja ki grafikonként a
    frontend/pages/2_Progression.py."""
    resp = _get(f"/exercises/{exercise_id}/progression", params={"lookback_weeks": lookback_weeks})
    resp.raise_for_status()
    return resp.json()

