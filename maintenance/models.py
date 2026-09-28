"""Plain data types shared across the maintenance control program.

Ezek a maintenance modul "közös nyelve": a scheduler, a decision, a
state store és a db_swap_controller mind ugyanezeket a típusokat adják
egymásnak át, hogy a folyamat minden állomása egyértelműen leírható és
tesztelhető legyen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# A csere lépései a befejezésük sorrendjében. A RunState.phase mindig
# az utoljára SIKERESEN befejezett lépést mutatja, így megszakadás után
# a workflow pontosan a következő lépéssel folytatja (nem az elejéről).
SWAP_PHASES: tuple[str, ...] = (
    "started",
    "prerequisites_ok",
    "writes_frozen",
    "backed_up",
    "rehearsal_verified",
    "old_instance_deleted",
    "target_provisioned",
    "restored",
    "repointed",
    "verified",
    "completed",
)

Phase = Literal[
    "idle",
    "started",
    "prerequisites_ok",
    "writes_frozen",
    "backed_up",
    "rehearsal_verified",
    "old_instance_deleted",
    "target_provisioned",
    "restored",
    "repointed",
    "verified",
    "completed",
    "halted",
]


@dataclass(frozen=True)
class CheckResult:
    """Result of one scheduled async status check against the provider.

    Ezt állítja elő a maintenance/workflow.py::check_instance_status, és
    ezt kapja meg bemenetként a maintenance/decision.py::decide_swap.
    """

    checked_at: datetime
    reachable: bool
    status: str | None = None
    expires_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class SwapDecision:
    """Whether a swap is warranted, why, and which maintenance request it serves.

    A `trigger_key` azonosítja a kiváltó karbantartási kérést (pl.
    "expiry:2026-10-01T00:00:00+00:00"); a sikeres futás ezt rögzíti,
    és ugyanaz a kérés többé nem indít cserét."""

    should_swap: bool
    reason: str
    trigger_key: str | None = None


@dataclass
class RunState:
    """Durable, resumable record of one swap run's progress.

    Ezt tárolja lemezen (JSON-ként) a maintenance/state.py::StateStore.
    A legutolsó befejezett futás egyben a "nyilvántartás" is: az
    `active_instance_id` mezője mondja meg, melyik példányt kell a
    következő ellenőrzésnek figyelnie.
    """

    run_id: str
    phase: Phase
    started_at: datetime
    updated_at: datetime
    active_instance_id: str | None = None
    target_instance_id: str | None = None
    backup_id: str | None = None
    trigger_key: str | None = None
    fulfilled_trigger: str | None = None
    halted_from: str | None = None
    error: str | None = None
    log: list[str] = field(default_factory=list)
