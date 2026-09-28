"""Practice restore into an isolated local database, before the old instance is deleted.

Section 6.3/3: a Render ingyenes csomagja workspace-enként egy példányt
enged, ezért a régit törölni kell az új létrehozása előtt. Mielőtt ezt
a visszafordíthatatlan lépést megtennénk, a mentést ugyanazzal a
`restore_database` művelettel visszaállítjuk egy helyi, elkülönített
Postgres adatbázisba (REHEARSAL_DATABASE_URL), és ellenőrizzük:
- a backend SAJÁT olvasó végpontjai (memóriában futtatva, erre az
  adatbázisra kötve) ugyanazt a pillanatképet adják, mint a forrás;
- egy új rekord beszúrása nem ütközik a visszaállított ID-kkal.
Csak ha mindkettő rendben van, törölhető a régi példány.
"""

from __future__ import annotations

import logging
from collections.abc import Generator

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from maintenance.backup import BackupMetadata, restore_database
from maintenance.snapshot import compare_snapshots, fetch_snapshot

logger = logging.getLogger(__name__)

ID_CHECK_EXERCISE = "__rehearsal_id_check__"


class RehearsalFailedError(RuntimeError):
    """The practice restore did not reproduce the source content."""


async def rehearse_restore(metadata: BackupMetadata, rehearsal_dsn: str) -> None:
    """Restore into the rehearsal DB and verify content, rule results and ID assignment."""
    # Csak itt importáljuk a backendet: a vezérlőprogram többi része
    # nem függ tőle, és a backend kódja nem fut a Render példányon.
    from backend.db import connect_args_for, get_db
    from backend.main import app
    from backend.maintenance_mode import require_writes_enabled

    restore_database(metadata, rehearsal_dsn)

    # Ugyanazok a kapcsolati paraméterek, mint a backendben (UTC időzóna),
    # hogy a pillanatképek időbélyegei azonosan szerializálódjanak.
    engine = create_engine(rehearsal_dsn, connect_args=connect_args_for(rehearsal_dsn))
    session_factory = sessionmaker(bind=engine, autoflush=False)

    def rehearsal_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = rehearsal_db
    app.dependency_overrides[require_writes_enabled] = lambda: None
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://rehearsal") as client:
            actual = await fetch_snapshot(client)
            differences = compare_snapshots(metadata.snapshot, actual)
            if differences:
                raise RehearsalFailedError("; ".join(differences))

            max_id = max((e["id"] for e in metadata.snapshot["exercises"]), default=0)
            resp = await client.post("/exercises", json={"name": ID_CHECK_EXERCISE, "category": "internal"})
            if resp.status_code != 201 or resp.json()["id"] <= max_id:
                raise RehearsalFailedError(f"new record insert collided with restored IDs: {resp.status_code} {resp.text}")
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_writes_enabled, None)
        engine.dispose()

    logger.info(
        "Rehearsal restore verified: %d exercises, %d sessions, identical progression results",
        len(actual["exercises"]),
        len(actual["sessions"]),
    )
