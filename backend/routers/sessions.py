"""Edzésalkalom (WorkoutSession) és fejlődés (progression) végpontok.

Ez a fájl mutatja meg legjobban a beadandó elvárt "teljes adatútját":
Streamlit -> ez a router (HTTP réteg) -> service/domain réteg
(feldolgozás) -> adatbázis -> és az eredmény vissza, ugyanezen az
úton, a Streamlit felé.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.maintenance_mode import require_writes_enabled
from backend.schemas import ProgressionPoint, ProgressionRead, WorkoutSessionCreate, WorkoutSessionRead
from backend.services.progression_analyzer import ProgressionAnalyzer
from backend.services.workout_service import UnknownExerciseError, create_workout_session_with_sets
from backend.services.workout_service import get_session, list_sessions

router = APIRouter(tags=["sessions"])


@router.post("/sessions", response_model=WorkoutSessionRead, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: WorkoutSessionCreate,
    db: Session = Depends(get_db),
    _: None = Depends(require_writes_enabled),
):
    """POST /sessions: a Streamlit "Edzésnapló" oldala (frontend/pages/
    1_Workout_Log.py) innen menti le az edzésalkalmat és a hozzá tartozó
    sorozatokat. Maga a mentés (tranzakció, több tábla) a
    backend/services/workout_service.py::create_workout_session_with_sets
    procedurális folyamatában történik; a router csak meghívja, és a
    dobott UnknownExerciseError-t alakítja 400-as HTTP hibává.
    """
    try:
        return create_workout_session_with_sets(db, payload)
    except UnknownExerciseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/sessions", response_model=list[WorkoutSessionRead])
def get_sessions(
    start_date: date | None = None,
    end_date: date | None = None,
    db: Session = Depends(get_db),
):
    """GET /sessions: edzésalkalmak listázása, opcionális dátumszűréssel."""
    return list_sessions(db, start_date=start_date, end_date=end_date)


@router.get("/sessions/{session_id}", response_model=WorkoutSessionRead)
def get_session_detail(session_id: int, db: Session = Depends(get_db)):
    """GET /sessions/{id}: egy edzésalkalom részletei (a hozzá tartozó
    sorozatokkal együtt, mivel a WorkoutSessionRead séma tartalmazza a
    `sets` listát)."""
    session_row = get_session(db, session_id)
    if session_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Session {session_id} not found")
    return session_row


@router.get("/exercises/{exercise_id}/progression", response_model=ProgressionRead)
def get_progression(exercise_id: int, lookback_weeks: int = 8, db: Session = Depends(get_db)):
    """GET /exercises/{id}/progression: a témaspecifikus döntési szabály
    (fejlődés/stagnálás/romlás) elérési útja HTTP-n keresztül.

    Adatáramlás: a router létrehoz egy ProgressionAnalyzer-t (OOP
    szolgáltatás, backend/services/progression_analyzer.py), meghívja az
    `analyze`-ot (ami az adatbázisból tölt, majd a tiszta
    backend/domain/progression.py::compute_progression függvényt hívja),
    és a kapott ProgressionResult mezőit átcsomagolja a válasz sémába
    (ProgressionRead) — ezt kapja meg végül JSON-ként a Streamlit
    "Progression" oldala (frontend/pages/2_Progression.py) a grafikonhoz.
    """
    analyzer = ProgressionAnalyzer(db)
    result = analyzer.analyze(exercise_id, lookback_weeks=lookback_weeks)
    return ProgressionRead(
        exercise_id=exercise_id,
        points=[ProgressionPoint(session_date=d, estimated_one_rm=v) for d, v in result.points],
        slope_per_week=result.slope_per_week,
        status=result.status,
        best_e1rm=result.best_e1rm,
        best_e1rm_date=result.best_e1rm_date,
    )
