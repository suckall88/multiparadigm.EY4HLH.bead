"""Object-oriented service wrapping the progression domain rule.

OOP paradigm: a class with real behavior (DB querying and mapping rows to
the pure domain type) that separates the database world from the pure
rule, rather than a plain data holder.

Ez a fájl az OOP paradigma egyik helye: a ProgressionAnalyzer egy
adatbázis-munkamenethez kötött szolgáltatás, ami összeköti a "koszos"
adatbázis-világot a tiszta (backend/domain/progression.py) függvényekkel.
Kérésenként egy példány készül (a router hozza létre), ezért nem tart
gyorsítótárat: minden válasz a friss adatbázis-állapotot tükrözi.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.domain.progression import LOOKBACK_WEEKS_DEFAULT, ProgressionResult, SetPoint, compute_progression
from backend.models import SetEntry, WorkoutSession


class ProgressionAnalyzer:
    """Loads an exercise's logged sets and runs the progression rule on them."""

    def __init__(self, db: Session):
        # A `db` a backend/db.py::get_db()-ből jövő, kérésenkénti Session —
        # a router (backend/routers/sessions.py) adja át létrehozáskor.
        self._db = db

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

    def analyze(self, exercise_id: int, lookback_weeks: int = LOOKBACK_WEEKS_DEFAULT) -> ProgressionResult:
        """Ezt hívja a backend/routers/sessions.py progresszió-végpontja.

        Adatáramlás: betölti a sorozatokat az adatbázisból
        (`_load_set_points`) -> átadja a tiszta `compute_progression`
        függvénynek (backend/domain/progression.py) -> visszaadja az
        eredményt, amit a router ProgressionRead sémába csomagol.
        """
        entries = self._load_set_points(exercise_id)
        return compute_progression(exercise_id=exercise_id, entries=entries, lookback_weeks=lookback_weeks)
