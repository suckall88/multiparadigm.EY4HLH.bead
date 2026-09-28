"""A fejlődés-elemzési döntési szabály (backend/domain/progression.py)
unit tesztjei: fix, kézzel kiszámolt bemenet/kimenet párokkal, az
adatbázis és a HTTP réteg teljes megkerülésével.
"""

from datetime import date

import pytest

from backend.domain.progression import (
    SetPoint,
    compute_progression,
    estimate_one_rm,
)


def test_estimate_one_rm_normal():
    # Epley: 100 * (1 + 5/30) = 116.666...
    # Kézzel kiszámolt várt érték, nem a függvény újrafuttatásából.
    assert estimate_one_rm(100.0, 5) == pytest.approx(116.6667, rel=1e-4)


@pytest.mark.parametrize("weight_kg, reps", [(100.0, 0), (100.0, 13), (0.0, 5), (-10.0, 5)])
def test_estimate_one_rm_invalid_reps(weight_kg, reps):
    # Egy paraméterezett teszt négy érvénytelen bemenetet fed le
    # egyszerre: 0 és 13 ismétlés (a megengedett 1-12 tartományon kívül),
    # illetve 0 és negatív súly.
    with pytest.raises(ValueError):
        estimate_one_rm(weight_kg, reps)


def test_compute_progression_insufficient_data():
    # Egyetlen bejegyzés sosem elég trendszámításhoz — a szabálynak
    # "insufficient_data"-t kell visszaadnia, de a legjobb e1RM-et
    # (ami egyetlen pontból is számolható) igen.
    entries = [SetPoint(session_date=date(2026, 1, 1), weight_kg=100.0, reps=5)]
    result = compute_progression(exercise_id=1, entries=entries)
    assert result.status == "insufficient_data"
    assert result.best_e1rm == pytest.approx(116.6667, rel=1e-4)


def test_compute_progression_no_valid_entries():
    # Edge case: 20 ismétlés kívül esik a megbízható (1-12) tartományon,
    # így ez a bejegyzés teljesen kiszűrődik — még a "legjobb e1RM" sem
    # számolható belőle.
    entries = [SetPoint(session_date=date(2026, 1, 1), weight_kg=100.0, reps=20)]
    result = compute_progression(exercise_id=1, entries=entries)
    assert result.status == "insufficient_data"
    assert result.best_e1rm is None


@pytest.mark.parametrize(
    "weights, expected_status",
    [
        ([100.0, 102.5, 105.0, 107.5], "improving"),
        ([100.0, 100.0, 100.0, 100.0], "plateau"),
        ([100.0, 97.5, 95.0, 92.5], "declining"),
    ],
)
def test_compute_progression_classifies_trend(weights, expected_status):
    # Három tipikus eset egy paraméterezett teszttel: emelkedő,
    # stagnáló és csökkenő súlysorozat — mindegyik heti bontásban,
    # ugyanazzal az ismétlésszámmal, hogy csak a súly-trend számítson.
    entries = [
        SetPoint(session_date=date(2026, 1, 1 + i * 7), weight_kg=w, reps=5)
        for i, w in enumerate(weights)
    ]
    result = compute_progression(
        exercise_id=1, entries=entries, reference_date=date(2026, 1, 22)
    )
    assert result.status == expected_status
