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

# A csere-folyamat lehetséges fázisai — a RunState.phase mindig ezek
# egyike, és sorban halad előre (idle -> ... -> completed), vagy
# "halted"-re ugrik hiba esetén.
Phase = Literal[
    "idle",
    "checking",
    "freezing",
    "backing_up",
    "provisioning",
    "restoring",
    "repointing",
    "completed",
    "halted",
]


@dataclass(frozen=True)
class CheckResult:
    """Result of one scheduled async status check against the provider.

    Ezt állítja elő a maintenance/provider_client.py aszinkron
    állapotellenőrzése (elérhető-e a szolgáltatás, mikor jár le), és
    ezt kapja meg bemenetként a maintenance/decision.py::decide_swap.
    """

    checked_at: datetime
    reachable: bool
    expires_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class SwapDecision:
    """Whether a swap is warranted, and why.

    A decide_swap függvény kimenete — a `reason` mezőt a logolás és a
    tesztek is felhasználják annak ellenőrzésére, hogy a helyes okból
    hozta-e meg a döntést a rendszer."""

    should_swap: bool
    reason: str


@dataclass
class RunState:
    """Durable, resumable record of one swap run's progress.

    Ezt tárolja lemezen (JSON-ként) a maintenance/state.py::StateStore —
    ha a program megszakadna egy csere közepén, ebből tudja
    a maintenance/db_swap_controller.py folytatni/rekonstruálni, hogy
    hol tartott (lásd Section 6.4: partial failure & safe continuation).
    """

    run_id: str
    phase: Phase
    started_at: datetime
    updated_at: datetime
    active_instance_id: str | None = None
    target_instance_id: str | None = None
    backup_id: str | None = None
    fulfilled_expiry_at: str | None = None
    error: str | None = None
    log: list[str] = field(default_factory=list)
