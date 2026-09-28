"""Durable, atomic run-state persistence and the single-instance lock.

Object-oriented paradigm: StateStore wraps the run-state file with real
behavior (atomic writes, typed load/save); SchedulerLock wraps an
OS-level file lock.

Ez adja a vezérlőprogram "memóriáját": ha a program megszakad (pl. a
gép újraindul csere közben), a lemezre írt run_state.json-ból tudja a
workflow, melyik lépés fejeződött be utoljára, és onnan folytatja.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import IO

from maintenance.models import RunState

logger = logging.getLogger(__name__)


class AnotherSchedulerRunningError(RuntimeError):
    """Egy második ütemező indítását akadályozza meg (Section 6.4: egyszerre
    csak egy csere futhat, és újraindítás nem indíthat csendben duplikált
    ütemezőt)."""


class StateStore:
    def __init__(self, state_dir: Path):
        self._state_file = state_dir / "run_state.json"

    def load(self) -> RunState | None:
        """A legutóbb mentett futásállapot, vagy None, ha még sosem volt futás."""
        if not self._state_file.exists():
            return None
        raw = json.loads(self._state_file.read_text(encoding="utf-8"))
        raw["started_at"] = datetime.fromisoformat(raw["started_at"])
        raw["updated_at"] = datetime.fromisoformat(raw["updated_at"])
        return RunState(**raw)

    def save(self, run_state: RunState) -> None:
        """Atomically persist run_state (temp file + fsync + os.replace).

        Ha a program pont írás közben szakad meg, vagy a régi, vagy az új
        tartalom van a fájlban — sosem egy félig kiírt, olvashatatlan JSON."""
        payload = asdict(run_state)
        payload["started_at"] = run_state.started_at.isoformat()
        payload["updated_at"] = run_state.updated_at.isoformat()

        tmp_path = self._state_file.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self._state_file)
        logger.info("Persisted run state: run_id=%s phase=%s", run_state.run_id, run_state.phase)


class SchedulerLock:
    """OS-level exclusive lock held for the scheduler process's whole lifetime.

    Az operációs rendszer a zárat a folyamat leállásakor (akár
    összeomláskor is) automatikusan feloldja, így egy megszakadt futás
    után nem marad "beragadt" zár, ami megakadályozná a folytatást."""

    def __init__(self, state_dir: Path):
        self._path = state_dir / "scheduler.lock"
        self._handle: IO[str] | None = None

    def acquire(self) -> None:
        handle = self._path.open("a+", encoding="utf-8")
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise AnotherSchedulerRunningError(f"Another maintenance scheduler holds {self._path}") from exc
        self._handle = handle

    def release(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
