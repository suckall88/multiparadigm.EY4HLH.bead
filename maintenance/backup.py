"""Backup and restore operations for the swap's source/target databases.

Procedural paradigm: each function is one well-defined step in the
backup/restore workflow, orchestrated by DBSwapController.

Ezek a függvények a Section 6.2 (Backup & restore) követelményt
teljesítik: a `pg_dump`/`pg_restore` külső eszközöket hívják meg Python
alfolyamatként (subprocess), a mentést tartósan, forrástól függetlenül,
időbélyeggel és forrás-azonosítóval ellátva tárolják lemezen.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from maintenance.config import BACKUP_DIR

logger = logging.getLogger(__name__)


class NoBackupAvailableError(RuntimeError):
    """Raised when a restore/recovery is attempted with no verified backup on disk.

    Ezt dobja az `ensure_backup_available`, ha nincs érvényes mentés —
    a hívónak (maintenance/db_swap_controller.py) ilyenkor le KELL
    állnia, sosem szabad kitalált/hamis adattal helyettesíteni a
    visszaállítást (Section 6.4 elvárása)."""


@dataclass(frozen=True)
class BackupMetadata:
    """Egy mentéshez tartozó, önmagában is értelmezhető adatlap: honnan
    (source_id), mikor (created_at) készült, hol van a dump fájl, és
    egy SHA-256 ellenőrzőösszeg, amivel a fájl sértetlensége igazolható."""

    source_id: str
    created_at: str
    dump_path: str
    sha256: str


def _sha256_of(path: Path) -> str:
    """A dump fájl tartalmából számol egy egyedi "ujjlenyomatot" — ez
    kerül a metaadatba, hogy később ellenőrizhető legyen, nem sérült-e
    meg a fájl a tárolás/átvitel során."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_database(source_dsn: str, source_id: str, backup_dir: Path = BACKUP_DIR) -> BackupMetadata:
    """Dump `source_dsn` via pg_dump and write a sidecar metadata JSON file.

    Bemenet: a forrás adatbázis kapcsolati sztringje (source_dsn) és
    azonosítója (source_id, pl. a Render service ID-ja). A `pg_dump`
    külső programot hívja meg (subprocess.run), ami a teljes séma +
    adattartalmat egy bináris `.dump` fájlba menti. Ezután elkészíti a
    hozzá tartozó BackupMetadata-t, és egy `.json` "sidecar" fájlba
    írja (ugyanolyan névvel, csak más kiterjesztéssel) — ezt olvassa
    vissza a `latest_backup`. Kimenet: a metaadat, amit a
    db_swap_controller a RunState.backup_id mezőjében tárol tovább.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump_path = backup_dir / f"{source_id}_{timestamp}.dump"

    subprocess.run(
        ["pg_dump", "--format=custom", f"--file={dump_path}", source_dsn],
        check=True,
        capture_output=True,
    )

    metadata = BackupMetadata(
        source_id=source_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        dump_path=str(dump_path),
        sha256=_sha256_of(dump_path),
    )
    metadata_path = dump_path.with_suffix(".json")
    metadata_path.write_text(json.dumps(metadata.__dict__, indent=2), encoding="utf-8")

    logger.info("Backed up %s to %s", source_id, dump_path)
    return metadata


def latest_backup(backup_dir: Path = BACKUP_DIR) -> BackupMetadata | None:
    """A legutóbb készült mentés metaadatát adja vissza (a fájlnevek
    időbélyeg szerint rendezhetők, ezért az ábécésorrend szerinti
    utolsó a legfrissebb is). None, ha még sosem készült mentés."""
    metadata_files = sorted(backup_dir.glob("*.json"))
    if not metadata_files:
        return None
    raw = json.loads(metadata_files[-1].read_text(encoding="utf-8"))
    return BackupMetadata(**raw)


def ensure_backup_available(metadata: BackupMetadata | None) -> BackupMetadata:
    """Raise NoBackupAvailableError rather than let a restore proceed with nothing.

    Explicit "kapu" a visszaállítás előtt: ha nincs metaadat, VAGY a
    hozzá tartozó fájl fizikailag hiányzik a lemezről, azonnal hibát
    dob — sosem enged tovább egy hiányos/hamis állapotot."""
    if metadata is None or not Path(metadata.dump_path).exists():
        raise NoBackupAvailableError("No verified backup is available to restore from")
    return metadata


def restore_database(metadata: BackupMetadata, target_dsn: str) -> None:
    """Restore a verified backup into an empty target database.

    A `pg_restore` külső programot hívja meg, ami a korábban mentett
    `.dump` fájl tartalmát (séma, sorok, kapcsolatok, ID-k) visszaírja
    a cél adatbázisba (target_dsn — jellemzően az újonnan létrehozott
    Render Postgres instance). Ezt hívja a
    maintenance/db_swap_controller.py a csere 4. lépésében, miután az
    ensure_backup_available már megerősítette, hogy van érvényes mentés.
    """
    ensure_backup_available(metadata)
    subprocess.run(
        ["pg_restore", "--clean", "--if-exists", "--no-owner", f"--dbname={target_dsn}", metadata.dump_path],
        check=True,
        capture_output=True,
    )
    logger.info("Restored %s into target database", metadata.dump_path)
