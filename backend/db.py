"""SQLAlchemy engine/session setup."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import DATABASE_URL

# SQLite-nál külön be kell kapcsolni, hogy több szálról/kérésből is
# lehessen ugyanazt a kapcsolatot használni (FastAPI request-enként
# más szálon futhat). Más adatbázisnál (pl. Postgres) erre nincs
# szükség, ezért ott üres marad az extra paraméter.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

# Az "engine" a tényleges adatbázis-kapcsolatot (connection pool-t) fogja
# össze. A DATABASE_URL-t a backend/config.py adja (ami meg a .env-ből
# vagy a Render környezeti változóiból jön) — így ugyanez a kód futhat
# lokális SQLite-tal és a felhős Postgresszel is, kód-módosítás nélkül.
engine = create_engine(DATABASE_URL, connect_args=_connect_args)

# SessionLocal egy "gyár": minden meghívásra egy új Session (adatbázis-
# munkamenet) objektumot ad vissza, ami az engine-hez van kötve.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Közös ős-osztály, ebből örököl minden ORM modell (backend/models.py).

    A SQLAlchemy ebből az osztályból tudja összegyűjteni az összes
    táblát (Base.metadata), amit a backend/main.py induláskor
    létrehoz az adatbázisban.
    """

    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI "dependency": minden bejövő HTTP kéréshez egy friss
    Session-t nyit, azt adja tovább a router-függvénynek (lásd
    backend/routers/*.py, ahol `db: Session = Depends(get_db)`), majd
    a kérés lezárása után (a `finally` ágban) mindenképp bezárja azt —
    így egy kérésen belüli adatbázis-műveletek egy tranzakcióba
    tartoznak, és nem szivárog a kapcsolat.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
