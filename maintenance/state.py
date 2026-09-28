"""Durable, atomic run-state persistence for the maintenance control program.

Object-oriented paradigm: StateStore is a stateful class wrapping the
run-state file and the concurrency lock file with real behavior (atomic
writes, lock acquisition, reconciliation-on-resume).

Ez adja a maintenance modul "memóriáját": ha a vezérlőprogram
megszakadna (pl. a gép újraindul csere közben), a lemezre írt
run_state.json-ból tudja a maintenance/db_swap_controller.py
rekonstruálni, hol tartott — enélkül minden megszakítás elveszett
állapotot és esetleg duplikált/inkonzisztens cserét jelentene.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from maintenance.config import STATE_DIR
from maintenance.models import RunState

logger = logging.getLogger(__name__)

STATE_FILE = STATE_DIR / "run_state.json"
LOCK_FILE = STATE_DIR / "run.lock"


class AnotherRunInProgressError(RuntimeError):
    """Akkor dobódik, ha valaki egy második cserét próbálna indítani,
    miközben már fut egy — ez biztosítja, hogy "egyszerre csak egy
    csere fusson" (Section 6.4 elvárása)."""

    pass


class StateStore:
    def __init__(self, state_file: Path = STATE_FILE, lock_file: Path = LOCK_FILE):
        self._state_file = state_file
        self._lock_file = lock_file

    # ---- run state persistence -------------------------------------------------

    def load(self) -> RunState | None:
        """Beolvassa a legutóbb mentett futásállapotot a JSON fájlból.
        None-t ad vissza, ha még sosem indult futás (nincs fájl) — ezt
        a maintenance/decision.py::decide_swap is megkapja, hogy tudja,
        van-e korábbi, "completed" állapotú futás."""
        if not self._state_file.exists():
            return None
        raw = json.loads(self._state_file.read_text(encoding="utf-8"))
        raw["started_at"] = datetime.fromisoformat(raw["started_at"])
        raw["updated_at"] = datetime.fromisoformat(raw["updated_at"])
        return RunState(**raw)

    def save(self, run_state: RunState) -> None:
        """Atomically persist run_state (temp-file + os.replace).

        Miért fontos az atomicitás: ha a program pont íráskor szakadna
        meg, egy félig kiírt JSON fájl olvashatatlanná tenné a teljes
        állapotot. Ehelyett előbb egy ideiglenes `.tmp` fájlba írunk,
        majd az `os.replace()` egyetlen, oszthatatlan lépésben cseréli
        le vele az eredeti fájlt — vagy a régi, vagy az új tartalom
        van ott mindig, sosem egy törött közbülső állapot.
        """
        payload = asdict(run_state)
        payload["started_at"] = run_state.started_at.isoformat()
        payload["updated_at"] = run_state.updated_at.isoformat()

        tmp_path = self._state_file.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp_path, self._state_file)
        logger.info("Persisted run state: run_id=%s phase=%s", run_state.run_id, run_state.phase)

    def clear(self) -> None:
        """Törli a mentett állapotot (pl. teszt-cleanupnál)."""
        if self._state_file.exists():
            self._state_file.unlink()

    # ---- concurrency lock --------------------------------------------------------

    def acquire_lock(self, run_id: str) -> None:
        """Létrehoz egy zárolás-fájlt a futó run_id-val. Ha már létezik
        egy lock-fájl (más futás van folyamatban), AnotherRunInProgressError-t
        dob — ezt a maintenance/db_swap_controller.py hívja meg egy új
        csere indítása előtt."""
        if self._lock_file.exists():
            holder = self._lock_file.read_text(encoding="utf-8").strip()
            raise AnotherRunInProgressError(f"Another maintenance run is in progress: {holder}")
        self._lock_file.write_text(run_id, encoding="utf-8")

    def release_lock(self) -> None:
        """Feloldja a zárolást — sikeres befejezés VAGY biztonságos
        halt (hiba) után is meg kell hívni, különben a program soha
        többé nem tudna új futást indítani."""
        if self._lock_file.exists():
            self._lock_file.unlink()

    def is_locked(self) -> bool:
        return self._lock_file.exists()
