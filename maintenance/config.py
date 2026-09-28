"""Environment-driven configuration for the maintenance control program.

Minden beállítás egyetlen, megváltoztathatatlan `MaintenanceSettings`
objektumba kerül (`load_settings()`), amit a scheduler egyszer olvas be
induláskor, és paraméterként ad tovább — így a tesztek saját,
kézzel összeállított beállításokkal futtathatják ugyanazt a kódot,
környezeti változók piszkálása nélkül.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from maintenance.decision import parse_maintenance_at

load_dotenv()

# A maintenance modul saját, a projekt gyökerétől független
# könyvtárszerkezete: mentések, futásállapot és logok — ezek mind
# helyi fájlok, nem függenek attól, hogy a backend/DB éppen fut-e
# (Section 6.4: a vezérlés nem függhet a cserélendő DB-től).
MAINTENANCE_DIR = Path(__file__).resolve().parent
BACKUP_DIR = MAINTENANCE_DIR / "backups"
STATE_DIR = MAINTENANCE_DIR / "state"
LOG_DIR = MAINTENANCE_DIR / "logs"

RENDER_API_BASE_URL = "https://api.render.com/v1"


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class MaintenanceSettings:
    """A vezérlőprogram teljes konfigurációja.

    `swap_enabled` az előzetes jogosítás (Section 6.1): amíg nincs
    kifejezetten bekapcsolva, a program csak ellenőriz és naplóz, de
    semmilyen felhős erőforráshoz nem nyúl.
    """

    backend_url: str
    maintenance_token: str
    render_api_key: str
    render_owner_id: str
    render_web_service_id: str
    active_db_instance_id: str
    rehearsal_database_url: str
    swap_enabled: bool
    check_interval_seconds: int
    expiry_warning_days: int
    maintenance_at: datetime | None
    timezone: ZoneInfo
    target_plan: str
    target_region: str
    target_pg_version: str
    write_drain_seconds: float
    backup_dir: Path = BACKUP_DIR
    state_dir: Path = STATE_DIR


def load_settings() -> MaintenanceSettings:
    """Beolvassa a beállításokat a környezeti változókból (.env vagy a
    futtató környezet), és létrehozza a helyi munkakönyvtárakat."""
    tz = ZoneInfo(os.getenv("TIMEZONE", "Europe/Budapest"))
    for directory in (BACKUP_DIR, STATE_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    return MaintenanceSettings(
        backend_url=os.getenv("BACKEND_URL", "http://localhost:8000"),
        maintenance_token=os.getenv("MAINTENANCE_TOKEN", "dev-maintenance-token"),
        render_api_key=os.getenv("RENDER_API_KEY", ""),
        render_owner_id=os.getenv("RENDER_OWNER_ID", ""),
        render_web_service_id=os.getenv("RENDER_WEB_SERVICE_ID", ""),
        active_db_instance_id=os.getenv("ACTIVE_DB_INSTANCE_ID", ""),
        rehearsal_database_url=os.getenv("REHEARSAL_DATABASE_URL", ""),
        swap_enabled=_env_bool("MAINTENANCE_SWAP_ENABLED"),
        check_interval_seconds=int(os.getenv("CHECK_INTERVAL_SECONDS", "3600")),
        expiry_warning_days=int(os.getenv("EXPIRY_WARNING_DAYS", "3")),
        # Időzóna nélkül megadott MAINTENANCE_AT a TIMEZONE szerint
        # értendő; minden összehasonlítás UTC-ben történik.
        maintenance_at=parse_maintenance_at(os.getenv("MAINTENANCE_AT", ""), tz),
        timezone=tz,
        # Az új példány paraméterei explicitek (Section 6.3/1), nem a
        # szolgáltató alapértékeire bízzuk őket.
        target_plan=os.getenv("TARGET_DB_PLAN", "free"),
        target_region=os.getenv("TARGET_DB_REGION", "frankfurt"),
        target_pg_version=os.getenv("TARGET_DB_VERSION", "16"),
        write_drain_seconds=float(os.getenv("WRITE_DRAIN_SECONDS", "5")),
    )
