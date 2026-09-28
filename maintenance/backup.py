"""Backup and restore operations for the swap's source/target databases.

Procedural paradigm: each function is one well-defined step in the
backup/restore workflow, orchestrated by DBSwapController.

A `pg_dump`/`pg_restore` külső eszközöket hívjuk Python alfolyamatként.
Egy mentés két fájlból áll a `maintenance/backups/` mappában:
- `<forrás>_<időbélyeg>.dump` — a pg_dump custom formátumú kimenete
  (séma, sorok, kapcsolatok, szekvenciák, azaz az ID-kiosztás állapota),
- `<forrás>_<időbélyeg>.json` — metaadat: forrás-azonosító, UTC
  időbélyeg, SHA-256 ellenőrzőösszeg és a mentéskori tartalmi
  pillanatkép (maintenance/snapshot.py), amihez a visszaállítást mérjük.
Kapcsolati adat/jelszó egyik fájlba sem kerül.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PG_TOOLS = ("pg_dump", "pg_restore")


class NoBackupAvailableError(RuntimeError):
    """Raised when a restore is attempted with no intact backup on disk.

    A hívónak ilyenkor le KELL állnia — sosem szabad kitalált adattal
    helyettesíteni a visszaállítást (Section 6.4)."""


class BackupToolError(RuntimeError):
    """pg_dump/pg_restore failed; carries the tool's stderr for the log."""


@dataclass(frozen=True)
class BackupMetadata:
    source_id: str
    created_at: str
    dump_path: str
    sha256: str
    snapshot: dict[str, Any]

    @property
    def metadata_path(self) -> str:
        return str(Path(self.dump_path).with_suffix(".json"))


def missing_pg_tools() -> list[str]:
    """Azok a külső eszközök, amelyek nincsenek a PATH-on (előfeltétel-ellenőrzéshez)."""
    return [tool for tool in PG_TOOLS if shutil.which(tool) is None]


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_tool(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        # A parancssor a DSN-t (jelszót) is tartalmazza, ezért csak az
        # eszköz nevét és a hibakimenetét naplózzuk.
        raise BackupToolError(f"{args[0]} exited with {result.returncode}: {result.stderr.strip()[:500]}")


def backup_database(
    source_dsn: str, source_id: str, snapshot: dict[str, Any], backup_dir: Path
) -> BackupMetadata:
    """Dump `source_dsn` with pg_dump and write the sidecar metadata JSON."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump_path = backup_dir / f"{source_id}_{timestamp}.dump"

    _run_tool(["pg_dump", "--format=custom", "--no-owner", "--no-acl", f"--file={dump_path}", source_dsn])

    metadata = BackupMetadata(
        source_id=source_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        dump_path=str(dump_path),
        sha256=_sha256_of(dump_path),
        snapshot=snapshot,
    )
    Path(metadata.metadata_path).write_text(json.dumps(asdict(metadata), indent=2), encoding="utf-8")
    logger.info("Backed up %s to %s (sha256=%s)", source_id, dump_path.name, metadata.sha256[:12])
    return metadata


def load_backup(metadata_path: str | None) -> BackupMetadata:
    """Load a backup's metadata and verify the dump file is present and intact.

    Explicit "kapu" minden visszaállítás előtt: hiányzó metaadat, hiányzó
    dump fájl vagy eltérő ellenőrzőösszeg esetén NoBackupAvailableError."""
    if not metadata_path or not Path(metadata_path).exists():
        raise NoBackupAvailableError(f"Backup metadata not found: {metadata_path}")
    metadata = BackupMetadata(**json.loads(Path(metadata_path).read_text(encoding="utf-8")))
    dump = Path(metadata.dump_path)
    if not dump.exists():
        raise NoBackupAvailableError(f"Backup dump file missing: {dump.name}")
    if _sha256_of(dump) != metadata.sha256:
        raise NoBackupAvailableError(f"Backup dump checksum mismatch: {dump.name}")
    return metadata


def restore_database(metadata: BackupMetadata, target_dsn: str) -> None:
    """Restore a verified backup into the target database.

    A `--clean --if-exists` miatt egy félbeszakadt visszaállítás
    megismételhető: a mentésben szereplő objektumokat előbb eldobja,
    így nem keletkezik duplikált adat. A szekvenciák értékét is
    visszaállítja, így az új rekordok ID-ja nem ütközik."""
    _run_tool(
        [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-acl",
            "--exit-on-error",
            f"--dbname={target_dsn}",
            metadata.dump_path,
        ]
    )
    logger.info("Restored %s into target database", Path(metadata.dump_path).name)
