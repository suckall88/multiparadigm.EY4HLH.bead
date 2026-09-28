"""Object-oriented service wrapping the progression domain rule.

OOP paradigm: a stateful class with real behavior (DB querying, mapping to
the pure domain type, and a small in-memory result cache) rather than a
plain data holder.

Ez a fájl az OOP paradigma egyik helye: a ProgressionAnalyzer osztály
valódi állapotot tart (adatbázis-kapcsolat + gyorsítótár), és ezen
keresztül köti össze a "koszos" adatbázis-világot a tiszta
(backend/domain/progression.py) függvényekkel.
"""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from backend.domain.progression import ProgressionResult, SetPoint, compute_progression
from backend.models import SetEntry, WorkoutSession


class ProgressionAnalyzer:
    """Computes and caches strength-progression results per exercise."""

    def __init__(self, db: Session, cache_ttl_seconds: float = 30.0):
        # A `db` a backend/db.py::get_db()-ből jövő Session — a router
        # (backend/routers/exercises.py) adja át ide létrehozáskor.
        self._db = db
        self._cache_ttl_seconds = cache_ttl_seconds
        # cache_key = (exercise_id, lookback_weeks) -> (mikor számoltuk, eredmény)
        self._cache: dict[tuple[int, int], tuple[float, ProgressionResult]] = {}

    def _load_set_points(self, exercise_id: int) -> list[SetPoint]:
        """Adatbázis-lekérdezés: az adott gyakorlathoz tartozó összes
        sorozatot (SetEntry) összeköti a hozzá tartozó edzésnappal
        (WorkoutSession.session_date), és a tiszta domain rétegnek
        megfelelő SetPoint objektumokká alakítja. Ez az egyetlen hely,
        ahol ez az osztály az adatbázissal beszél.
        """
        rows = (
            self._db.query(WorkoutSession.session_date, SetEntry.weight_kg, SetEntry.reps)
            .join(SetEntry, SetEntry.session_id == WorkoutSession.id)
            .filter(SetEntry.exercise_id == exercise_id)
            .all()
        )
        return [
            SetPoint(session_date=session_date, weight_kg=weight_kg, reps=reps)
            for session_date, weight_kg, reps in rows
        ]

    def analyze(self, exercise_id: int, lookback_weeks: int = 8) -> ProgressionResult:
        """Ezt hívja a backend/routers/exercises.py végpontja.

        Adatáramlás: gyorsítótár-ellenőrzés -> ha nincs friss találat,
        betölti a sorozatokat az adatbázisból (`_load_set_points`) ->
        átadja a tiszta `compute_progression` függvénynek (backend/domain/
        progression.py) -> az eredményt gyorsítótárazza és visszaadja.
        A cache azért kell, hogy egymást gyorsan követő kérések (pl. a
        Streamlit többszöri újrarajzolása) ne futtassák le feleslegesen
        újra a lekérdezést + számítást.
        """
        cache_key = (exercise_id, lookback_weeks)
        cached = self._cache.get(cache_key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < self._cache_ttl_seconds:
            return cached[1]

        entries = self._load_set_points(exercise_id)
        result = compute_progression(
            exercise_id=exercise_id, entries=entries, lookback_weeks=lookback_weeks
        )
        self._cache[cache_key] = (now, result)
        return result
