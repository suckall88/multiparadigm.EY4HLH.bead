"""SQLAlchemy ORM models for the workout log."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


def _utcnow() -> datetime:
    # Mindig UTC időzónás időbélyeget ad vissza — ezt használják a
    # created_at/updated_at oszlopok alapértékként, hogy a szerver
    # időzónájától függetlenül konzisztens legyen az adat.
    return datetime.now(timezone.utc)


class Exercise(Base):
    """Egy gyakorlat törzsadata (pl. "Fekvenyomás"). Ez a tábla a
    "szótár": a tényleges edzés-bejegyzések (SetEntry) erre hivatkoznak
    az exercise_id idegen kulccsal.
    """

    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Egy Exercise-hez sok SetEntry tartozhat (1:N kapcsolat).
    # A `back_populates` a SetEntry.exercise mezővel köti össze a két
    # irányt, hogy pl. exercise.sets automatikusan lekérdezze a
    # hozzá tartozó sorozatokat.
    sets: Mapped[list["SetEntry"]] = relationship(back_populates="exercise")


class WorkoutSession(Base):
    """Egy edzésalkalom (nap + megjegyzés), amihez több SetEntry
    (sorozat) tartozik. Ezt hozza létre a
    backend/services/workout_service.py `create_session` függvénye,
    amikor a Streamlit felület POST-ol a /sessions végpontra.
    """

    __tablename__ = "workout_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    # cascade="all, delete-orphan": ha egy WorkoutSession-t törlünk,
    # a hozzá tartozó SetEntry sorok is automatikusan törlődnek —
    # nem maradnak árva sorozat-rekordok az adatbázisban.
    sets: Mapped[list["SetEntry"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class SetEntry(Base):
    """Egy konkrét sorozat (hány kg-mal, hány ismétlést, hányadik
    sorozatként). Ez a "tényadat" tábla: innen olvassa ki a
    backend/domain/progression.py az e1RM-számításhoz szükséges
    (weight_kg, reps) párokat.
    """

    __tablename__ = "set_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ForeignKey: melyik edzésalkalomhoz (WorkoutSession) és melyik
    # gyakorlathoz (Exercise) tartozik ez a sorozat.
    session_id: Mapped[int] = mapped_column(ForeignKey("workout_sessions.id"), nullable=False)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"), nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    reps: Mapped[int] = mapped_column(Integer, nullable=False)
    set_number: Mapped[int] = mapped_column(Integer, nullable=False)
    rpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped[WorkoutSession] = relationship(back_populates="sets")
    exercise: Mapped[Exercise] = relationship(back_populates="sets")

