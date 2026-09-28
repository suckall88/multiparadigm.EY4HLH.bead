"""Pydantic request/response models for the API.

Ezek NEM az adatbázis-táblák (azok a backend/models.py-ban vannak),
hanem a "külvilág" (Streamlit / bármilyen HTTP kliens) felé mutatott
alak. A routerek (backend/routers/*.py) ezekkel validálják a bejövő
JSON-t, illetve ezekké alakítják az ORM-objektumokat a válaszban.
A "...Create" sémák a bemenetet, a "...Read" sémák a kimenetet írják le.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


# ---- Exercises ----------------------------------------------------------


class ExerciseCreate(BaseModel):
    """Bejövő adat egy új gyakorlat létrehozásához (POST /exercises).

    A Field(min_length=..., max_length=...) automatikusan 422-es hibát
    ad, ha a kliens (Streamlit) hibás/hiányos adatot küld — ezt még a
    router-kód lefutása előtt a FastAPI ellenőrzi.
    """

    name: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=50)


class ExerciseRead(BaseModel):
    """Kimenő adat: ez jelenik meg a válaszban, amikor egy Exercise
    ORM-objektumot (backend/models.py::Exercise) adunk vissza.
    `from_attributes=True` engedi, hogy közvetlenül egy ORM-objektumból
    (nem csak dict-ből) építse fel magát a séma.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str
    created_at: datetime


# ---- Workout sessions & sets ---------------------------------------------


class SetEntryCreate(BaseModel):
    """Egy sorozat bemeneti adata, ami egy WorkoutSessionCreate
    listájának eleme lehet (beágyazott edzésnapló-bevitel)."""

    exercise_id: int
    weight_kg: float = Field(gt=0)
    reps: int = Field(gt=0)
    set_number: int = Field(gt=0)
    rpe: float | None = Field(default=None, ge=0, le=10)


class SetEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exercise_id: int
    weight_kg: float
    reps: int
    set_number: int
    rpe: float | None
    created_at: datetime


class WorkoutSessionCreate(BaseModel):
    """Egy teljes edzésalkalom bevitele: dátum + megjegyzés + a
    hozzá tartozó sorozatok listája egyben. Ezt küldi a Streamlit
    "Edzésnapló" oldala (frontend/pages/1_Workout_Log.py) a
    POST /sessions híváskor; a backend/services/workout_service.py
    dolgozza fel, és menti le az adatbázisba (WorkoutSession + SetEntry sorok).
    """

    session_date: date
    notes: str | None = None
    sets: list[SetEntryCreate] = Field(default_factory=list)


class WorkoutSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_date: date
    notes: str | None
    created_at: datetime
    updated_at: datetime
    sets: list[SetEntryRead]


# ---- Progression ----------------------------------------------------------


class ProgressionPoint(BaseModel):
    """Egy pont a fejlődési grafikonon: egy edzésnap és az aznapi
    becsült egy ismétléses maximum (e1RM)."""

    session_date: date
    estimated_one_rm: float


class ProgressionRead(BaseModel):
    """A GET /exercises/{id}/progression válasza. A `points` listát,
    a `slope_per_week` trendet és a `status` besorolást a
    backend/domain/progression.py tiszta függvényei számolják ki,
    a backend/services/progression_analyzer.py::ProgressionAnalyzer
    hívja meg őket, a router pedig ebbe a sémába csomagolja a választ.
    """

    exercise_id: int
    points: list[ProgressionPoint]
    slope_per_week: float | None
    status: str
    best_e1rm: float | None
    best_e1rm_date: date | None

