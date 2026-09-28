"""Environment-driven configuration for the backend."""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Beolvassa a projekt gyökerében lévő .env fájlt, és a benne lévő
# kulcs=érték párokat beteszi a folyamat környezeti változói közé
# (os.environ). Ha nincs .env, ez nem hibázik, csak nem csinál semmit.
load_dotenv()


# os.getenv(NÉV, alapérték): ha a NÉV nevű környezeti változó be van
# állítva (pl. a Render felületén vagy a .env fájlban), azt használja,
# egyébként az alapértéket. Ezeket a konstansokat importálja be a
# backend/db.py (DATABASE_URL), a backend/logging_config.py (LOG_LEVEL)
# és a backend/routers/internal.py (MAINTENANCE_TOKEN) — innen kapják
# meg a saját beállításukat, így a kapcsolati adatok nincsenek
# "beégetve" a kódba.
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./dev.db")
MAINTENANCE_TOKEN: str = os.getenv("MAINTENANCE_TOKEN", "dev-maintenance-token")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
# DB-csere közben a maintenance program a Render környezeti változóban
# is bekapcsolja az írászárat, így egy újraindult/újra deployolt backend
# is zárolt írással indul (Section 6.3/2).
WRITES_FROZEN_AT_STARTUP: bool = os.getenv("WRITES_FROZEN", "false").strip().lower() in ("1", "true", "yes")
