"""Environment-driven configuration for the maintenance control program."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

# A maintenance modul saját, a projekt gyökerétől független
# könyvtárszerkezete: mentések, futásállapot és logok — ezek mind
# helyi fájlok, nem függenek attól, hogy a backend/DB éppen fut-e
# (lásd Section 6.4: a vezérlés nem függhet a cserélendő DB-től).
MAINTENANCE_DIR = Path(__file__).resolve().parent
BACKUP_DIR = MAINTENANCE_DIR / "backups"
STATE_DIR = MAINTENANCE_DIR / "state"
LOG_DIR = MAINTENANCE_DIR / "logs"

# Hova küldje a maintenance program a belső (tokenes) kéréseket, és
# milyen tokennel azonosítsa magát — ugyanaz a MAINTENANCE_TOKEN, mint
# amit a backend/config.py is beolvas, így a kettő egyezni tud.
BACKEND_URL: str = os.getenv("BACKEND_URL", "http://localhost:8000")
MAINTENANCE_TOKEN: str = os.getenv("MAINTENANCE_TOKEN", "dev-maintenance-token")

# Render API hitelesítő adatok — ezeket a maintenance/provider_client.py
# és a maintenance/backend_admin.py használja a Render felhő API
# hívásaihoz (új DB-instance létrehozás, backend env-változó frissítés).
RENDER_API_KEY: str = os.getenv("RENDER_API_KEY", "")
RENDER_OWNER_ID: str = os.getenv("RENDER_OWNER_ID", "")
RENDER_WEB_SERVICE_ID: str = os.getenv("RENDER_WEB_SERVICE_ID", "")
RENDER_API_BASE_URL: str = "https://api.render.com/v1"

# Az ütemezett ellenőrzés gyakorisága (mp) és hány nappal lejárat előtt
# jelezzen "közelgő lejárat" állapotot (Section 6.1/6.2 szabály).
CHECK_INTERVAL_SECONDS: int = int(os.getenv("CHECK_INTERVAL_SECONDS", "3600"))
EXPIRY_WARNING_DAYS: int = int(os.getenv("EXPIRY_WARNING_DAYS", "3"))
# Ha be van állítva, ez a konfigurált időpont helyettesíti a valós
# lejárat kivárását (tesztelhető rehearsal-hoz) — lásd maintenance/decision.py.
_maintenance_at_raw = os.getenv("MAINTENANCE_AT", "").strip()
MAINTENANCE_AT: str | None = _maintenance_at_raw or None

# Minden időpont-összehasonlítás ebben az egy, konzisztens időzónában
# történik (Section 6.2 elvárása), hogy ne legyen kétértelmű a
# "közelgő lejárat" kiszámítása.
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/Budapest"))

# Ha a könyvtárak még nem léteznek (első futás), létrehozzuk őket —
# ez a modul betöltésekor egyszer lefut.
for _dir in (BACKUP_DIR, STATE_DIR, LOG_DIR):
    _dir.mkdir(parents=True, exist_ok=True)
