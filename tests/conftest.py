"""Shared pytest fixtures: an isolated, recreatable SQLite test database.

A conftest.py egy pytest-speciális fájlnév: az itt definiált
fixture-öket (`db_session`, `client`) BÁRMELYIK tesztfájl automatikusan
megkaphatja paraméterként, import nélkül — ez a "megosztott teszt-
infrastruktúra" (Section 4 elvárása: közös pytest tesztkészlet).
"""

from __future__ import annotations

import os

# A backend modulok importálása ELŐTT: a tesztek (és az app induláskori
# táblalétrehozása) sose érjék el a fejlesztői dev.db-t vagy a .env-ben
# megadott adatbázist, és egy lokálisan beállított írászár se szivárogjon be.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["WRITES_FROZEN"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from backend.db import Base, get_db  # noqa: E402
from backend.main import app  # noqa: E402
from backend.maintenance_mode import maintenance_mode_state  # noqa: E402


@pytest.fixture()
def db_session():
    """Minden tesztesethez egy vadonatúj, memóriában élő SQLite
    adatbázist hoz létre (":memory:") — ez izolált és gyorsan
    eldobható, sosem éri el a valós fejlesztői/éles adatbázist.
    `StaticPool` kell hozzá, mert a memóriában lévő SQLite alapból
    szálanként külön adatbázis lenne; ezzel egyetlen kapcsolatot
    tartunk életben a teszt teljes időtartamára.
    A teszt lefutása UTÁN (a `finally` ágban) bezárja a Session-t és
    eldobja az összes táblát — a következő teszt már egy teljesen
    tiszta adatbázisból indul.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    """Egy FastAPI TestClient-et ad, ami a `db_session` fixture teszt-
    adatbázisát használja a valós helyett.

    A `dependency_overrides[get_db] = override_get_db` sor az a
    "trükk", amivel az API integrációs teszt a VALÓDI routereken és
    feldolgozó kódon megy keresztül (backend/routers/*.py,
    backend/services/*.py), csak az adatbázis-kapcsolatot cseréljük le
    a teszt-adatbázisra — ahogy a beadandó elvárja: a feldolgozási
    logika nincs kicserélve/mockolva, csak a DB-kapcsolat.
    """
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    # Biztosítjuk, hogy egy előző teszt esetleges write-freeze állapota
    # ne szivárogjon át erre a tesztre (a maintenance_mode_state
    # modul-szintű, folyamatszintű singleton).
    maintenance_mode_state.unfreeze()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
