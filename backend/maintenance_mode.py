"""In-process write-freeze flag used during a Section-6 DB swap.

The maintenance control program flips this via an authenticated internal
endpoint before taking the final backup, and only releases it once the
swap has fully succeeded (see maintenance/db_swap_controller.py).
"""

from __future__ import annotations

from fastapi import HTTPException, status


class MaintenanceModeState:
    """Egyszerű, folyamaton belüli (nem adatbázisban tárolt) állapot:
    egyetlen igaz/hamis kapcsoló, hogy éppen zárolva vannak-e az írások.
    Egy darab példány (lásd lentebb `maintenance_mode_state`) él belőle
    a teljes backend-folyamat alatt, ezt osztja meg minden kérés.
    """

    def __init__(self) -> None:
        self._writes_frozen = False

    @property
    def writes_frozen(self) -> bool:
        return self._writes_frozen

    def freeze(self) -> None:
        """A maintenance vezérlőprogram hívja (a belső, tokennel védett
        /internal végponton keresztül, lásd backend/routers/internal.py)
        közvetlenül a végső biztonsági mentés előtt: mostantól minden
        írási kérés 503-at kap, amíg fel nem oldják."""
        self._writes_frozen = True

    def unfreeze(self) -> None:
        """A csere sikeres lezárása után oldja fel a zárolást."""
        self._writes_frozen = False


# Modul-szintű, egyetlen (singleton) példány — minden router ugyanezt
# az objektumot importálja, így mindenki ugyanazt az állapotot látja.
maintenance_mode_state = MaintenanceModeState()


def require_writes_enabled() -> None:
    """FastAPI dependency-ként használják az író végpontok (POST/PUT/…):
    ha épp zárolva vannak az írások, azonnal 503-as hibát dob, mielőtt
    a router-függvény törzse egyáltalán lefutna — így egy DB-csere
    közben nem tud véletlenül új adat bekerülni az adatbázisba.
    """
    if maintenance_mode_state.writes_frozen:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Writes are temporarily frozen for scheduled database maintenance.",
        )
