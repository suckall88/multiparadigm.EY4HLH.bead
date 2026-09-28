"""Procedural top-level maintenance workflow: check -> decide -> backup -> provision -> restore -> repoint.

Procedural paradigm: a single, followable, function-decomposed flow, not a
class - orchestration only, all real logic lives in the collaborators it
calls (decision.py, DBSwapController, RenderClient).

Ez a modul a maintenance rendszer "karmestere": ő hívja meg sorban az
összes darabot (döntés, zárolás, mentés, létrehozás, visszaállítás,
átirányítás), de maga nem tartalmaz üzleti logikát — az mind a hívott
modulokban van. Ezt hívja meg periodikusan a maintenance/scheduler.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from maintenance.backend_admin import set_maintenance_mode
from maintenance.backup import NoBackupAvailableError, latest_backup
from maintenance.config import MAINTENANCE_AT, RENDER_API_KEY
from maintenance.db_swap_controller import DBSwapController, SwapHaltedError
from maintenance.decision import decide_swap
from maintenance.models import CheckResult, RunState
from maintenance.provider_client import RenderClient, RenderApiError
from maintenance.state import AnotherRunInProgressError, StateStore

logger = logging.getLogger(__name__)


async def check_instance_status(client: httpx.AsyncClient, instance_id: str) -> CheckResult:
    """A real async status check against the Render API (Section 6.1).

    Ez az EGYETLEN "valódi async" művelet, amit a beadandó Section 6.1
    kifejezetten megkövetel: `await client.get(...)` egy igazi hálózati
    hívás a Render API felé (nem szimulált sleep). Sikeres válasz esetén
    kiolvassa a lejárati dátumot (`expiresAt`); hálózati hiba esetén nem
    dob kivételt, hanem egy `reachable=False` CheckResult-tal tér vissza
    — ezt a maintenance/decision.py::decide_swap kapja meg bemenetként.
    """
    now = datetime.now(timezone.utc)
    try:
        resp = await client.get(
            f"https://api.render.com/v1/postgres/{instance_id}",
            headers={"Authorization": f"Bearer {RENDER_API_KEY}"},
        )
        resp.raise_for_status()
        data = resp.json()
        expires_at_raw = data.get("expiresAt")
        expires_at = datetime.fromisoformat(expires_at_raw) if expires_at_raw else None
        return CheckResult(checked_at=now, reachable=True, expires_at=expires_at)
    except httpx.HTTPError as exc:
        logger.warning("Status check failed for instance %s: %s", instance_id, exc)
        return CheckResult(checked_at=now, reachable=False, error=str(exc))


def _parse_maintenance_at() -> datetime | None:
    """A konfigurált MAINTENANCE_AT környezeti változót (ha van)
    alakítja igazi datetime objektummá — ezt kapja meg a decide_swap,
    hogy próbafuttatáshoz felülírhassa a valós lejárat-alapú döntést."""
    if MAINTENANCE_AT is None:
        return None
    return datetime.fromisoformat(MAINTENANCE_AT)


async def run_maintenance_cycle(
    active_instance_id: str,
    source_dsn: str,
    state_store: StateStore | None = None,
) -> RunState:
    """One full check-and-maybe-swap cycle. Safe to call on a fixed schedule.

    Ezt a függvényt hívja a maintenance/scheduler.py egy `while True`
    ciklusban, `CHECK_INTERVAL_SECONDS` másodpercenként. A teljes
    lépéssor:
    1) betölti az esetleg megszakadt korábbi futást (state_store.load),
    2) elvégzi a valódi async állapotellenőrzést (check_instance_status),
    3) meghozza a döntést (decide_swap) — ha nem kell csere és nincs
       folyamatban lévő futás, azonnal visszatér ("idle"),
    4) ha kell csere (vagy folytatni kell egyet), zárolást szerez
       (hogy csak egy csere fusson egyszerre), rekonstruálja az
       állapotot (`controller.reconcile`), majd sorban végrehajtja az
       5 lépést a DBSwapController-en keresztül,
    5) sikeres befejezéskor elmenti, hogy ez a konkrét lejárati
       időpont "fulfilled" (ne induljon újra csere ugyanazért),
    6) bármilyen ismert hiba esetén biztonságosan leáll ("halted"),
       sosem talál ki adatot vagy próbál automatikusan tovább menni,
    7) a `finally` ág mindig feloldja a zárolást, függetlenül attól,
       hogy siker vagy hiba volt-e.
    """
    state_store = state_store or StateStore()

    # Ha van korábbi, még be nem fejezett futás, azt folytatjuk —
    # nem indítunk egy vadonatúj futást a régi helyett.
    existing = state_store.load()
    if existing is not None and existing.phase not in ("completed", "halted"):
        logger.info("Resuming interrupted run %s (phase=%s)", existing.run_id, existing.phase)
        run_state = existing
    else:
        run_state = None

    async with httpx.AsyncClient(timeout=10.0) as http_client:
        check = await check_instance_status(http_client, active_instance_id)
        decision = decide_swap(check, run_state, datetime.now(timezone.utc), maintenance_at=_parse_maintenance_at())
        logger.info("Swap decision: should_swap=%s reason=%s", decision.should_swap, decision.reason)

        # Ha nincs folyamatban lévő futás ÉS a döntés szerint nem kell
        # csere, nincs több teendő ebben a ciklusban.
        if not decision.should_swap and run_state is None:
            idle = RunState(
                run_id="none",
                phase="idle",
                started_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                active_instance_id=active_instance_id,
            )
            return idle

        provider = RenderClient(client=http_client)
        controller = DBSwapController(
            provider=provider,
            state_store=state_store,
            http_client=http_client,
            source_dsn=source_dsn,
            source_id=active_instance_id,
        )

        # Zárolás megszerzése: ha már fut egy másik csere (pl. egy
        # átfedő ütemezett hívás), ez a ciklus egyszerűen kihagyja
        # magát, nem próbálja meg párhuzamosan is végrehajtani.
        try:
            state_store.acquire_lock(run_state.run_id if run_state else "pending")
        except AnotherRunInProgressError:
            logger.warning("Another maintenance run is already in progress; skipping this cycle")
            return run_state or RunState(
                run_id="skipped",
                phase="idle",
                started_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

        try:
            if run_state is None:
                run_state = controller._new_run_state()
                run_state.active_instance_id = active_instance_id

            # Megszakadt futás esetén előbb egyeztet a valós
            # szolgáltatói állapottal (lásd DBSwapController.reconcile
            # docstringjét), mielőtt bármit is újra végrehajtana.
            run_state = await controller.reconcile(run_state) if run_state.phase != "idle" else run_state
            if run_state.phase == "halted":
                return run_state

            # Az öt hivatalos csere-lépés, sorban:
            run_state = controller.check_prerequisites(run_state)  # 1. előfeltételek
            run_state = await controller.freeze_writes_and_backup(run_state)  # 2. zárolás + mentés
            backup = latest_backup()
            run_state = await controller.provision_target(run_state)  # 3. új instance létrehozása
            run_state = await controller.create_and_restore(run_state, backup)  # 4. visszaállítás

            provisioned = await provider.get_postgres_instance(run_state.target_instance_id)
            new_dsn = provisioned["connectionInfo"]["externalConnectionString"]
            run_state = await controller.repoint_and_verify(run_state, new_dsn)  # 5. átirányítás + ellenőrzés

            # Megjegyezzük, hogy EZ a konkrét lejárati időpont már
            # "el van intézve" — a legközelebbi ellenőrzés ugyanerre az
            # expires_at-ra már nem fog újra cserét indítani
            # (lásd maintenance/decision.py 2. szabálya).
            if check.expires_at is not None:
                run_state.fulfilled_expiry_at = check.expires_at.isoformat()
                state_store.save(run_state)

            return run_state

        except (NoBackupAvailableError, SwapHaltedError, RenderApiError) as exc:
            # Bármely ismert, "biztonságosan kezelendő" hiba esetén nem
            # próbálunk automatikusan javítani vagy visszagörgetni —
            # csak naplózzuk, elmentjük a "halted" állapotot a hibával
            # együtt, és egy embernek kell utánanéznie.
            logger.error("Maintenance swap halted: %s", exc)
            run_state = run_state or controller._new_run_state()
            run_state.phase = "halted"
            run_state.error = str(exc)
            run_state.updated_at = datetime.now(timezone.utc)
            state_store.save(run_state)
            # Safely leave writes frozen or unfrozen depending on how far we got;
            # never auto-rollback or fabricate data - a human must follow up.
            return run_state
        finally:
            # Ez a sor MINDIG lefut (siker, kezelt hiba, vagy akár
            # nem várt kivétel esetén is), így a zárolás sosem ragad
            # be véglegesen egy hibás futás után.
            state_store.release_lock()
