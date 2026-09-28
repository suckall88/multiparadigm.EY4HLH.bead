"""Internal, token-authenticated endpoints used only by the maintenance program.

Ezeket a végpontokat NEM a Streamlit hívja, hanem a maintenance
vezérlőprogram (maintenance/backend_admin.py) egy DB-csere közben —
ezért van rajtuk egy külön, egyszerű token-alapú védelem (nem a
felhasználói auth része), hogy senki más ne tudja véletlenül/rosszul
zárolni az írásokat vagy próba-írást indítani.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config import MAINTENANCE_TOKEN
from backend.db import get_db
from backend.maintenance_mode import maintenance_mode_state
from backend.models import Exercise

router = APIRouter(prefix="/internal", tags=["internal"])

WRITE_CHECK_NAME = "__maintenance_write_check__"


class MaintenanceModeRequest(BaseModel):
    enabled: bool


class MaintenanceModeResponse(BaseModel):
    writes_frozen: bool


def _check_token(x_maintenance_token: str | None) -> None:
    """Összeveti a kérésben kapott `X-Maintenance-Token` fejlécet a
    szerver oldali MAINTENANCE_TOKEN-nel (backend/config.py, ami a
    környezeti változóból jön) — eltérés esetén 401-et dob."""
    if x_maintenance_token != MAINTENANCE_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid maintenance token")


@router.post("/maintenance-mode", response_model=MaintenanceModeResponse)
def set_maintenance_mode(
    payload: MaintenanceModeRequest,
    x_maintenance_token: str | None = Header(default=None),
) -> MaintenanceModeResponse:
    """A maintenance vezérlőprogram (maintenance/db_swap_controller.py)
    hívja meg a csere legelső lépéseként (`enabled=True`), hogy zárolja
    az írásokat (backend/maintenance_mode.py::maintenance_mode_state),
    majd a csere végén (`enabled=False`) oldja fel."""
    _check_token(x_maintenance_token)
    if payload.enabled:
        maintenance_mode_state.freeze()
    else:
        maintenance_mode_state.unfreeze()
    return MaintenanceModeResponse(writes_frozen=maintenance_mode_state.writes_frozen)


@router.get("/maintenance-mode", response_model=MaintenanceModeResponse)
def get_maintenance_mode() -> MaintenanceModeResponse:
    """Lekérdezhető, van-e éppen írászárolás — token nélkül is (csak
    olvasás, nem módosít semmit)."""
    return MaintenanceModeResponse(writes_frozen=maintenance_mode_state.writes_frozen)


class WriteCheckResponse(BaseModel):
    writable: bool


@router.post("/write-check", response_model=WriteCheckResponse)
def write_check(
    db: Session = Depends(get_db),
    x_maintenance_token: str | None = Header(default=None),
) -> WriteCheckResponse:
    """One controlled test write/delete, authorized via the maintenance token,
    used to confirm the (new) database is writable while normal application
    writes stay blocked by maintenance mode.

    A DB-csere 5. lépésénél (repointolás után) ezt hívja a maintenance
    program: létrehoz egy "próba" Exercise sort, majd rögtön törli is —
    ezzel bizonyítva, hogy az ÚJ adatbázis valóban írható, miközben a
    normál alkalmazás-írások továbbra is zárolva maradnak
    (require_writes_enabled csak a nyilvános végpontokra vonatkozik,
    erre a belső, tokenes végpontra nem).
    """
    _check_token(x_maintenance_token)
    # Egy korábbi, félbeszakadt ellenőrzés maradványát előbb eltávolítjuk,
    # különben az egyedi név miatt a beszúrás hibára futna.
    db.query(Exercise).filter(Exercise.name == WRITE_CHECK_NAME).delete()
    probe = Exercise(name=WRITE_CHECK_NAME, category="internal")
    db.add(probe)
    db.commit()
    db.delete(probe)
    db.commit()
    return WriteCheckResponse(writable=True)
