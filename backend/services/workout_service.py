"""Procedural workflow for recording a workout session with its sets.

Procedural paradigm: a single, followable, function-decomposed flow
(validate -> create session -> create sets -> commit -> refresh -> return).

Ez a fájl a PROCEDURÁLIS paradigma legtisztább példája a projektben:
egy jól követhető, lépésekre bontott munkafolyamat, amit a
backend/routers/sessions.py hív meg a POST /sessions végponton.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from backend.models import Exercise, SetEntry, WorkoutSession
from backend.schemas import WorkoutSessionCreate

logger = logging.getLogger(__name__)


class UnknownExerciseError(ValueError):
    """Akkor dobja a kód, ha a beküldött sorozatok olyan exercise_id-ra
    hivatkoznak, ami nem létezik az `exercises` táblában — a router ezt
    fogja el és alakítja 400-as HTTP hibává."""

    def __init__(self, exercise_id: int):
        super().__init__(f"Exercise {exercise_id} does not exist")
        self.exercise_id = exercise_id


def _validate_exercises_exist(db: Session, exercise_ids: set[int]) -> None:
    """1. lépés: még mielőtt bármit létrehoznánk, leellenőrzi, hogy a
    kérésben szereplő összes exercise_id valóban létezik-e."""
    found_ids = {
        row.id for row in db.query(Exercise.id).filter(Exercise.id.in_(exercise_ids)).all()
    }
    missing = exercise_ids - found_ids
    if missing:
        raise UnknownExerciseError(next(iter(missing)))


def _create_session_row(db: Session, session_date: date, notes: str | None) -> WorkoutSession:
    """2. lépés: létrehozza magát az edzésalkalom sort. A `db.flush()`
    kiküldi az INSERT-et az adatbázisnak (de még nem commitol), hogy a
    session_row.id már elérhető legyen a következő lépéshez (a
    sorozatoknak kell a session_id idegen kulcs)."""
    session_row = WorkoutSession(session_date=session_date, notes=notes)
    db.add(session_row)
    db.flush()  # assign session_row.id without committing yet
    return session_row


def _create_set_rows(db: Session, session_row: WorkoutSession, payload: WorkoutSessionCreate) -> None:
    """3. lépés: minden beküldött sorozatból létrehoz egy SetEntry sort,
    a most kapott session_row.id-vel összekötve."""
    for set_payload in payload.sets:
        db.add(
            SetEntry(
                session_id=session_row.id,
                exercise_id=set_payload.exercise_id,
                weight_kg=set_payload.weight_kg,
                reps=set_payload.reps,
                set_number=set_payload.set_number,
                rpe=set_payload.rpe,
            )
        )


def create_workout_session_with_sets(db: Session, payload: WorkoutSessionCreate) -> WorkoutSession:
    """Create a workout session together with all of its logged sets.

    Ez a belépési pont, amit a router hív. Adatáramlás:
    Streamlit -> POST /sessions (JSON) -> FastAPI validálja
    WorkoutSessionCreate sémával -> ez a függvény: (1) ellenőrzi a
    gyakorlat-hivatkozásokat, (2) létrehozza az edzésalkalmat, (3)
    létrehozza a sorozatokat, (4) egy tranzakcióban commitol -> ha
    bármi hiba történik, minden visszavonódik (rollback) -> sikeres
    esetben frissíti és visszaadja a teljes WorkoutSession-t, amit a
    router WorkoutSessionRead sémává alakítva küld vissza JSON-ban.
    """
    exercise_ids = {s.exercise_id for s in payload.sets}
    if exercise_ids:
        _validate_exercises_exist(db, exercise_ids)

    session_row = _create_session_row(db, payload.session_date, payload.notes)
    _create_set_rows(db, session_row, payload)

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to commit workout session for date %s", payload.session_date)
        raise

    db.refresh(session_row)
    logger.info("Created workout session %s with %d sets", session_row.id, len(payload.sets))
    return session_row


def list_sessions(
    db: Session, start_date: date | None = None, end_date: date | None = None
) -> list[WorkoutSession]:
    """Edzésalkalmak listázása, opcionális dátumszűréssel (a Streamlit
    "Edzésnapló" oldala hívja, ha a felhasználó szűkíti az időszakot)."""
    query = db.query(WorkoutSession)
    if start_date is not None:
        query = query.filter(WorkoutSession.session_date >= start_date)
    if end_date is not None:
        query = query.filter(WorkoutSession.session_date <= end_date)
    return query.order_by(WorkoutSession.session_date.desc()).all()


def get_session(db: Session, session_id: int) -> WorkoutSession | None:
    """Egy adott edzésalkalom lekérése ID alapján (részletnézethez)."""
    return db.get(WorkoutSession, session_id)
