"""Procedural top-level maintenance workflow: check -> decide -> run the swap steps.

Procedural paradigm: a single, followable, function-decomposed flow, not a
class - orchestration only; the decision is a pure function
(decision.py) and the swap steps live in DBSwapController.

Egy ciklus:
1) betölti a legutóbbi futást (ebből tudja, melyik az aktív példány),
2) elvégzi a valódi async állapotellenőrzést és naplózza az eredményt,
3) ha nincs folyamatban futás: dönt (decide_swap), és csak jogosított
   (MAINTENANCE_SWAP_ENABLED) esetben indít cserét,
4) megszakadt futásnál előbb egyeztet (reconcile), majd az utoljára
   befejezett lépés UTÁNI lépéstől folytatja,
5) bármilyen hiba esetén biztonságosan leáll ("halted"); egy leállt
   futás után a program semmit nem csinál, amíg egy ember a
   `--resume` kapcsolóval jóvá nem hagyja a folytatást.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import httpx

from maintenance.config import MaintenanceSettings
from maintenance.db_swap_controller import DBSwapController, Step
from maintenance.decision import decide_swap
from maintenance.models import SWAP_PHASES, CheckResult, RunState
from maintenance.provider_client import RenderApiError, RenderClient
from maintenance.state import StateStore

logger = logging.getLogger(__name__)


async def check_instance_status(provider: RenderClient, instance_id: str) -> CheckResult:
    """A real async status check against the Render API (Section 6.1).

    Hálózati/API hiba esetén nem dob kivételt, hanem `reachable=False`
    eredményt ad — ezt a decide_swap sosem tekinti csere-indoknak."""
    now = datetime.now(timezone.utc)
    if not instance_id:
        return CheckResult(checked_at=now, reachable=False, error="no active instance configured")
    try:
        instance = await provider.get_postgres(instance_id)
    except (RenderApiError, httpx.HTTPError) as exc:
        return CheckResult(checked_at=now, reachable=False, error=str(exc))
    expires_raw = instance.get("expiresAt")
    return CheckResult(
        checked_at=now,
        reachable=instance.get("status") == "available",
        status=instance.get("status"),
        expires_at=datetime.fromisoformat(expires_raw) if expires_raw else None,
        error=None if instance.get("status") == "available" else f"instance status {instance.get('status')}",
    )


def _new_run(active_instance_id: str, trigger_key: str | None, previous: RunState | None) -> RunState:
    now = datetime.now(timezone.utc)
    return RunState(
        run_id=str(uuid.uuid4()),
        phase="started",
        started_at=now,
        updated_at=now,
        active_instance_id=active_instance_id,
        trigger_key=trigger_key,
        # Az előzőleg teljesített kérés megmarad, amíg ez a futás be nem fejeződik.
        fulfilled_trigger=previous.fulfilled_trigger if previous else None,
    )


def _remaining_steps(controller: DBSwapController, phase: str) -> list[tuple[str, Step]]:
    """Azok a lépések, amelyek a `phase` (utoljára befejezett lépés) után jönnek."""
    done = SWAP_PHASES.index(phase)
    return [(name, step) for name, step in controller.steps() if SWAP_PHASES.index(name) > done]


async def run_maintenance_cycle(
    settings: MaintenanceSettings,
    provider: RenderClient,
    controller: DBSwapController,
    state_store: StateStore,
    now: datetime | None = None,
) -> RunState | None:
    """One full check-and-maybe-swap cycle. Safe to call on a fixed schedule.

    A visszatérési érték a futás állapota, vagy None, ha nem kellett cserélni."""
    last = state_store.load()
    if last is not None and last.phase == "halted":
        logger.error(
            "Previous swap run %s is halted after '%s' (%s); no action until an operator runs --resume",
            last.run_id,
            last.halted_from,
            last.error,
        )
        return last

    active_id = (last.active_instance_id if last else None) or settings.active_db_instance_id
    in_progress = last if last is not None and last.phase not in ("completed", "idle") else None

    check = await check_instance_status(provider, active_id)
    logger.info(
        "Status check: instance=%s reachable=%s status=%s expires_at=%s error=%s",
        active_id,
        check.reachable,
        check.status,
        check.expires_at,
        check.error,
    )

    if in_progress is not None:
        logger.info("Resuming interrupted run %s after step '%s'", in_progress.run_id, in_progress.phase)
        run_state = in_progress
    else:
        decision = decide_swap(
            check,
            last,
            now or datetime.now(timezone.utc),
            maintenance_at=settings.maintenance_at,
            expiry_warning_days=settings.expiry_warning_days,
        )
        logger.info("Swap decision: should_swap=%s reason=%s", decision.should_swap, decision.reason)
        if not decision.should_swap:
            return None
        if not settings.swap_enabled:
            logger.warning("Swap warranted but MAINTENANCE_SWAP_ENABLED is not set; not touching any resource")
            return None
        run_state = _new_run(active_id, decision.trigger_key, last)
        state_store.save(run_state)

    try:
        if in_progress is not None:
            run_state = await controller.reconcile(run_state)
            if run_state.phase == "halted":
                return run_state
        for _phase, step in _remaining_steps(controller, run_state.phase):
            run_state = await step(run_state)
        logger.info("Swap run %s completed; active instance is now %s", run_state.run_id, run_state.active_instance_id)
        return run_state
    except Exception as exc:  # noqa: BLE001 - every failure must end in a durable, safe halt
        return controller.halt(run_state, f"{type(exc).__name__}: {exc}")


def resume_halted_run(state_store: StateStore) -> RunState | None:
    """Operator command: after manual inspection, let the next cycle continue a halted run.

    A futás visszakerül az utoljára befejezett lépésre (`halted_from`);
    a következő ciklus előbb egyeztet (reconcile), és onnan folytat."""
    run_state = state_store.load()
    if run_state is None or run_state.phase != "halted":
        return None
    run_state.phase = run_state.halted_from or "started"  # type: ignore[assignment]
    run_state.error = None
    run_state.updated_at = datetime.now(timezone.utc)
    run_state.log.append(f"{run_state.updated_at.isoformat()} resumed by operator")
    state_store.save(run_state)
    return run_state
