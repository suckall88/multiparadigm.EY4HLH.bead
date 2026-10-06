"""Pure functions for computing strength-progression trends from workout sets.

Functional paradigm: every function here is deterministic and side-effect
free (no I/O, no mutation of its inputs).

Ez a fájl a FUNKCIONÁLIS paradigma helye a projektben: minden itteni
függvény "tiszta" — ugyanazokra a bemenetekre mindig ugyanazt a
kimenetet adja, nem nyúl adatbázishoz/hálózathoz/fájlhoz, és nem
módosítja a paraméterként kapott adatokat (a dataclass-ok "frozen=True",
tehát létrehozás után nem is lehetne módosítani őket).
Az adatot a backend/services/progression_analyzer.py::ProgressionAnalyzer
tölti be az adatbázisból, ALAKÍTJA ÁT ide bemenetté (SetPoint lista),
meghívja ezeket a függvényeket, és a kimenetet (ProgressionResult)
csomagolja vissza a backend/schemas.py::ProgressionRead sémába a válaszhoz.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import fmean
from typing import Literal

# A lehetséges trend-besorolások — a compute_progression végén ebből
# választ egyet a döntési szabály.
ProgressionStatus = Literal["improving", "plateau", "declining", "insufficient_data"]

MIN_REPS = 1
MAX_REPS = 12
MIN_SESSIONS_REQUIRED = 3
LOOKBACK_WEEKS_DEFAULT = 8
TREND_THRESHOLD_FRACTION = 0.005  # +/-0.5% of mean e1RM per week


@dataclass(frozen=True)
class SetPoint:
    """A single logged set, reduced to what the progression rule needs.

    Ez a "bemeneti" adatforma: a service réteg (nem ez a fájl!) tölti fel
    az adatbázisból lekérdezett SetEntry sorokból (backend/models.py).
    """

    session_date: date
    weight_kg: float
    reps: int


@dataclass(frozen=True)
class ProgressionResult:
    """A teljes elemzés kimenete egy gyakorlatra: a napi legjobb
    e1RM-pontok, a heti trend meredeksége, a szöveges státusz és a
    valaha mért legjobb érték. Ezt adja vissza compute_progression,
    és ebből építi fel a router a HTTP-választ.
    """

    exercise_id: int
    points: list[tuple[date, float]]
    slope_per_week: float | None
    status: ProgressionStatus
    best_e1rm: float | None
    best_e1rm_date: date | None


def estimate_one_rm(weight_kg: float, reps: int) -> float:
    """Estimate a one-rep max using the Epley formula.

    Only reliable for `MIN_REPS <= reps <= MAX_REPS`; higher-rep sets make
    the linear extrapolation inaccurate, so callers must stay in that range.

    Bemenet: egy sorozat súlya (kg) és ismétlésszáma.
    Kimenet: a becsült egy-ismétléses maximum (e1RM), kg-ban.
    Ezt hívja meg soronként a _best_e1rm_per_session lentebb, minden
    egyes naplózott sorozatra.
    """
    if weight_kg <= 0:
        raise ValueError("weight_kg must be positive")
    if not (MIN_REPS <= reps <= MAX_REPS + 1):
        raise ValueError(f"reps must be between {MIN_REPS} and {MAX_REPS}")
    return weight_kg * (1 + reps / 30)


def linear_trend(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Least-squares slope and intercept for (x, y) points.

    Returns (0.0, y) of the single point when fewer than two points are
    given; callers needing a "not enough data" signal must check the count
    themselves before relying on the slope.

    Klasszikus legkisebb négyzetek módszere: (nap-eltolás, e1RM) pontpárokra
    illeszt egyenest, hogy megkapjuk, naponta átlagosan mennyivel nő/csökken
    az e1RM. A compute_progression ezt hívja meg, majd az eredményt
    (naponkénti meredekség) szorozza fel hetivé (* 7).
    """
    if len(points) < 2:
        return 0.0, points[0][1] if points else 0.0

    x_mean = fmean(p[0] for p in points)
    y_mean = fmean(p[1] for p in points)

    numerator = sum((x - x_mean) * (y - y_mean) for x, y in points)
    denominator = sum((x - x_mean) ** 2 for x, _ in points)

    if denominator == 0:
        return 0.0, y_mean

    slope = numerator / denominator
    intercept = y_mean - slope * x_mean
    return slope, intercept


def _best_e1rm_per_session(entries: list[SetPoint]) -> dict[date, float]:
    """Segédfüggvény: napi bontásban megtartja a legjobb (legnagyobb)
    becsült e1RM-et, mert egy edzésen belül több sorozat is lehet
    ugyanahhoz a gyakorlathoz — a trendhez naponta csak egy pont kell.
    A `compute_progression` hívja meg elsőként a kapott bejegyzésekre.
    """
    best: dict[date, float] = {}
    for entry in entries:
        if not (MIN_REPS <= entry.reps <= MAX_REPS):
            continue
        e1rm = estimate_one_rm(entry.weight_kg, entry.reps)
        if entry.session_date not in best or e1rm > best[entry.session_date]:
            best[entry.session_date] = e1rm
    return best


def compute_progression(
    exercise_id: int,
    entries: list[SetPoint],
    reference_date: date | None = None,
    lookback_weeks: int = LOOKBACK_WEEKS_DEFAULT,
) -> ProgressionResult:
    """Classify an exercise's strength trend from its logged sets.

    Groups sets by session (best estimated 1RM per day), fits a linear
    trend over the lookback window, and classifies the slope relative to
    the mean e1RM. Needs at least `MIN_SESSIONS_REQUIRED` sessions inside
    the lookback window, otherwise reports `insufficient_data`.

    Ez a projekt TÉMASPECIFIKUS DÖNTÉSI SZABÁLYA: bemenetként egy
    gyakorlathoz tartozó összes naplózott sorozatot kapja (SetPoint
    lista), kimenetként pedig eldönti, hogy fejlődik, stagnál vagy
    romlik-e az adott gyakorlatban a teljesítmény.
    Hívási lánc: backend/services/progression_analyzer.py -> ez a
    függvény -> visszatér a ProgressionAnalyzer-nek -> az becsomagolja
    ProgressionRead sémába -> router visszaküldi JSON-ban a Streamlitnek.
    """
    per_session = _best_e1rm_per_session(entries)

    # Ha egyáltalán nincs érvényes (reps-tartományba eső) bejegyzés,
    # nem tudunk trendet számolni.
    if not per_session:
        return ProgressionResult(
            exercise_id=exercise_id,
            points=[],
            slope_per_week=None,
            status="insufficient_data",
            best_e1rm=None,
            best_e1rm_date=None,
        )

    # A referencia-dátum alapesetben a legutolsó edzésnap; innen
    # visszafelé nézzük a `lookback_weeks` hetes ablakot.
    ref = reference_date or max(per_session)
    cutoff_days = lookback_weeks * 7
    windowed = {
        d: e1rm for d, e1rm in per_session.items() if (ref - d).days <= cutoff_days
    }

    # A minden idők legjobb e1RM-je a TELJES (nem csak az ablakon
    # belüli) történelemből számít.
    best_date = max(per_session, key=lambda d: per_session[d])
    best_e1rm = per_session[best_date]

    # Ha túl kevés edzésnap esik az ablakba, nem lenne megbízható a
    # trendvonal — inkább "insufficient_data"-t adunk vissza, mint
    # félrevezető eredményt.
    if len(windowed) < MIN_SESSIONS_REQUIRED:
        return ProgressionResult(
            exercise_id=exercise_id,
            points=sorted(per_session.items()),
            slope_per_week=None,
            status="insufficient_data",
            best_e1rm=best_e1rm,
            best_e1rm_date=best_date,
        )

    # A dátumokat "napok száma az első pont óta" (x) értékké alakítjuk,
    # hogy a linear_trend egyszerű számokkal tudjon dolgozni.
    sorted_points = sorted(windowed.items())
    first_day = sorted_points[0][0]
    xy_points = [((d - first_day).days, e1rm) for d, e1rm in sorted_points]

    slope_per_day, _ = linear_trend(xy_points)
    slope_per_week = slope_per_day * 7

    # A küszöböt az átlagos e1RM %-ában határozzuk meg (relatív, nem
    # abszolút kg-ban), hogy kis és nagy súlyoknál is arányos legyen.
    mean_e1rm = fmean(e1rm for _, e1rm in sorted_points)
    threshold = mean_e1rm * TREND_THRESHOLD_FRACTION

    if slope_per_week > threshold:
        status: ProgressionStatus = "improving"
    elif slope_per_week < -threshold:
        status = "declining"
    else:
        status = "plateau"

    return ProgressionResult(
        exercise_id=exercise_id,
        points=sorted(per_session.items()),
        slope_per_week=slope_per_week,
        status=status,
        best_e1rm=best_e1rm,
        best_e1rm_date=best_date,
    )
