"""Gyakorlat-törzsadat végpontok: /exercises alatt.

A routerek felelőssége csak a HTTP-réteg (bemenet validálása, hibakód
kiválasztása, válasz összeállítása) — a tényleges munkát a
backend/services/*.py végzi. A Streamlit (frontend/api_client.py)
ezeket a végpontokat hívja HTTP-n keresztül.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.maintenance_mode import require_writes_enabled
from backend.models import Exercise
from backend.schemas import ExerciseCreate, ExerciseRead

router = APIRouter(prefix="/exercises", tags=["exercises"])


@router.post("", response_model=ExerciseRead, status_code=status.HTTP_201_CREATED)
def create_exercise(
    payload: ExerciseCreate,
    db: Session = Depends(get_db),
    _: None = Depends(require_writes_enabled),
) -> Exercise:
    """POST /exercises: új gyakorlat felvétele.

    `payload` már a FastAPI által validált ExerciseCreate (backend/schemas.py).
    `db` a backend/db.py::get_db()-től kapott, kérésenkénti Session.
    `require_writes_enabled` egy védelmi "dependency": ha épp DB-csere
    miatt zárolva vannak az írások (backend/maintenance_mode.py), ez a
    function-törzs le sem fut, egyből 503-at kap a kliens.
    Duplikált név esetén (a `name` oszlop egyedi) az adatbázis
    `IntegrityError`-t dob; ezt 409 Conflict válasszá alakítjuk,
    érthető hibaüzenettel, a tranzakció visszagörgetése után.
    """
    exercise = Exercise(name=payload.name, category=payload.category)
    db.add(exercise)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"Exercise '{payload.name}' already exists")
    db.refresh(exercise)
    # A visszaadott Exercise ORM-objektumot a FastAPI a response_model
    # (ExerciseRead) alapján automatikusan JSON-ná alakítja.
    return exercise


@router.get("", response_model=list[ExerciseRead])
def list_exercises(category: str | None = None, db: Session = Depends(get_db)) -> list[Exercise]:
    """GET /exercises: gyakorlatok listázása, opcionális kategória-szűréssel
    (pl. ?category=Láb). Ezt hívja a Streamlit legördülő menője, amikor
    kiválaszthatóvá teszi a gyakorlatokat."""
    query = db.query(Exercise)
    if category is not None:
        query = query.filter(Exercise.category == category)
    return query.order_by(Exercise.name).all()
