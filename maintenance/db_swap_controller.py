"""Stateful controller executing the five swap steps from the assignment spec.

OOP paradigm: DBSwapController owns run_id/phase state and one method per
swap step, with real behavior coordinating the provider client, backup
functions, and the backend's internal endpoints.

Ez a fájl valósítja meg a Section 6.3-ban leírt 5 lépéses csere-
folyamatot. Minden metódus egy-egy lépésnek felel meg, és mindegyik a
kapott RunState-et frissíti + lemezre menti (StateStore-on keresztül),
mielőtt a következő lépésre lépne — így ha bármelyik lépés közben
megszakadna a program, a maintenance/workflow.py::run_maintenance_cycle
tudja, honnan kell folytatni (lásd `reconcile`).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import httpx

from maintenance.backend_admin import set_maintenance_mode, verify_restored_content, verify_writable
from maintenance.backup import BackupMetadata, backup_database, ensure_backup_available, restore_database
from maintenance.config import RENDER_OWNER_ID, RENDER_WEB_SERVICE_ID
from maintenance.models import RunState
from maintenance.provider_client import RenderClient
from maintenance.state import StateStore

logger = logging.getLogger(__name__)

DEFAULT_PLAN = "free"
DEFAULT_REGION = "frankfurt"
DEFAULT_PG_VERSION = "16"


class SwapHaltedError(RuntimeError):
    """Raised when the controller cannot safely continue and halts the run.

    Ezt kapja el a maintenance/workflow.py, és állítja a futásállapotot
    "halted"-ra — SOSEM próbál automatikusan helyrehozni egy ilyen
    hibát, mert az kockázatosabb lenne, mint egy dokumentált, kézi
    beavatkozást váró leállás."""


class DBSwapController:
    def __init__(
        self,
        provider: RenderClient,
        state_store: StateStore,
        http_client: httpx.AsyncClient,
        source_dsn: str,
        source_id: str,
    ):
        # provider: a Render API async klienshez (maintenance/provider_client.py).
        # state_store: a futásállapot lemezre mentéséhez (maintenance/state.py).
        # http_client: a SAJÁT backendünk belső végpontjainak hívásához
        # (maintenance/backend_admin.py) — ez más, mint a `provider` HTTP klientje.
        self._provider = provider
        self._state = state_store
        self._http = http_client
        self._source_dsn = source_dsn
        self._source_id = source_id

    def _new_run_state(self) -> RunState:
        """Egy vadonatúj csere-futás azonosítóját (UUID) és kezdő
        állapotát hozza létre."""
        now = datetime.now(timezone.utc)
        return RunState(run_id=str(uuid.uuid4()), phase="idle", started_at=now, updated_at=now)

    def _save(self, run_state: RunState, phase: str, **updates: object) -> RunState:
        """Közös segédfüggvény minden lépés végén: beállítja az új
        fázist, frissíti az időbélyeget, alkalmazza az esetleges extra
        mezőváltozásokat (pl. backup_id=...), a lépést hozzáfűzi a
        futás naplójához (`run_state.log`), majd MINDIG lemezre menti
        (self._state.save) — ez biztosítja, hogy soha ne vesszen el
        egy lépés eredménye, ha a program a következő lépés közben áll le.
        """
        run_state.phase = phase  # type: ignore[assignment]
        run_state.updated_at = datetime.now(timezone.utc)
        for key, value in updates.items():
            setattr(run_state, key, value)
        run_state.log.append(f"{run_state.updated_at.isoformat()}: {phase}")
        self._state.save(run_state)
        return run_state

    # ---- Step 1: prerequisites --------------------------------------------------

    def check_prerequisites(self, run_state: RunState) -> RunState:
        """1. lépés (Section 6.3/1): mielőtt bármit is csinálnánk,
        ellenőrzi, hogy minden szükséges konfiguráció (Render tulajdonos
        és service azonosító) megvan-e. Ha hiányzik valami, azonnal
        SwapHaltedError-t dob — nem indul el félkész beállításokkal."""
        missing = [
            name
            for name, value in [("RENDER_OWNER_ID", RENDER_OWNER_ID), ("RENDER_WEB_SERVICE_ID", RENDER_WEB_SERVICE_ID)]
            if not value
        ]
        if missing:
            raise SwapHaltedError(f"Missing required configuration: {', '.join(missing)}")
        logger.info(
            "Prerequisites OK; targeting plan=%s region=%s version=%s", DEFAULT_PLAN, DEFAULT_REGION, DEFAULT_PG_VERSION
        )
        return self._save(run_state, "checking")

    # ---- Step 2: freeze writes + final backup -----------------------------------

    async def freeze_writes_and_backup(self, run_state: RunState) -> RunState:
        """2. lépés: ELŐSZÖR zárolja az írásokat a saját backendünkön
        (maintenance/backend_admin.py::set_maintenance_mode ->
        backend/routers/internal.py -> backend/maintenance_mode.py),
        és CSAK EZUTÁN készíti el a végleges biztonsági mentést
        (maintenance/backup.py::backup_database) — ez a sorrend
        garantálja, hogy a mentés a "befagyasztott" állapotot rögzíti,
        nem eshet bele egy közben érkező írás."""
        await set_maintenance_mode(self._http, enabled=True)
        run_state = self._save(run_state, "freezing")

        metadata = backup_database(self._source_dsn, self._source_id)
        run_state = self._save(run_state, "backing_up", backup_id=metadata.dump_path)
        return run_state

    # ---- Step 3: provision (or reuse) the target instance -----------------------

    async def provision_target(self, run_state: RunState) -> RunState:
        """3. lépés: mivel a Render free tier csak egy aktív Postgres-
        instance-t enged workspace-enként, előbb törli a régi
        instance-t (ha van), majd létrehozza az újat a megadott
        plan/region/verzió paraméterekkel. Az új instance ID-ját
        elmenti a run_state.target_instance_id mezőbe."""
        if run_state.active_instance_id:
            await self._provider.delete_postgres_instance(run_state.active_instance_id)
            logger.info("Deleted old instance %s (free-tier one-instance limit)", run_state.active_instance_id)

        new_instance = await self._provider.create_postgres_instance(
            name=f"edzesnaplo-db-{datetime.now(timezone.utc):%Y%m%d%H%M%S}",
            plan=DEFAULT_PLAN,
            region=DEFAULT_REGION,
            postgres_version=DEFAULT_PG_VERSION,
            owner_id=RENDER_OWNER_ID,
        )
        run_state = self._save(run_state, "provisioning", target_instance_id=new_instance["id"])
        return run_state

    # ---- Step 4: create + restore ------------------------------------------------

    async def create_and_restore(self, run_state: RunState, backup: BackupMetadata | None) -> RunState:
        """4. lépés: megvárja (async pollozással), amíg az új instance
        használhatóvá válik, majd a korábban készült mentést
        visszaállítja bele (maintenance/backup.py::restore_database).
        Az `ensure_backup_available` hívás garantálja, hogy csak
        VALÓS, ellenőrzött mentésből állítunk vissza — sosem folytatja
        a folyamatot, ha a `backup` paraméter None vagy a fájl hiányzik."""
        verified_backup = ensure_backup_available(backup)  # raises NoBackupAvailableError if missing

        instance = await self._provider.wait_until_available(run_state.target_instance_id)
        restore_database(verified_backup, instance["connectionInfo"]["externalConnectionString"])
        run_state = self._save(run_state, "restoring")
        return run_state

    # ---- Step 5: repoint backend + verify ----------------------------------------

    async def repoint_and_verify(self, run_state: RunState, new_dsn: str) -> RunState:
        """5. lépés: átírja a backend Render service DATABASE_URL
        környezeti változóját az ÚJ adatbázisra, újra-deployolja a
        szolgáltatást, és megvárja, amíg a deploy éles lesz. Ezután
        KÉT külön ellenőrzést végez: egy olvasási próbát
        (verify_restored_content — valódi adatot kér le, nem csak
        health-check) és egy írási próbát (verify_writable). Csak ha
        MINDKETTŐ sikeres, jelöli a futást "completed"-nek, és csak
        ekkor oldja fel az írászárolást — így a normál felhasználói
        írások a teljes ellenőrzés alatt is blokkolva maradnak."""
        await self._provider.update_service_env_var(RENDER_WEB_SERVICE_ID, "DATABASE_URL", new_dsn)
        deploy = await self._provider.trigger_deploy(RENDER_WEB_SERVICE_ID)
        await self._provider.wait_until_deploy_live(RENDER_WEB_SERVICE_ID, deploy["id"])

        if not await verify_restored_content(self._http):
            raise SwapHaltedError("Post-swap read check did not return expected content")
        if not await verify_writable(self._http):
            raise SwapHaltedError("Post-swap write check failed")

        run_state = self._save(
            run_state,
            "completed",
            active_instance_id=run_state.target_instance_id,
            target_instance_id=None,
        )

        await set_maintenance_mode(self._http, enabled=False)
        return run_state

    # ---- Resume-after-interruption reconciliation --------------------------------

    async def reconcile(self, run_state: RunState) -> RunState:
        """On restart with an in-progress run, verify provider state before acting
        instead of blindly retrying (which could create a duplicate instance).

        Ha a program egy megszakadt futással indul újra (a fázis nem
        "completed"/"halted"), ez a metódus MEGKÉRDEZI a Rendert, hogy
        a célinstance ténylegesen létezik-e és elérhető-e — csak ez
        alapján dönt, sosem tippel. Ha az instance már elérhető, a
        futás onnan folytatódik, ahol tartott (nem hoz létre egy
        felesleges második instance-t); ha nem sikerül lekérdezni az
        állapotot, biztonságosan leáll ("halted"), és kézi
        beavatkozást kér."""
        if run_state.phase in ("provisioning", "restoring", "repointing") and run_state.target_instance_id:
            try:
                instance = await self._provider.get_postgres_instance(run_state.target_instance_id)
            except Exception:
                logger.warning("Could not reconcile target instance %s; halting", run_state.target_instance_id)
                return self._save(run_state, "halted", error="Could not verify target instance state on resume")

            if instance.get("status") == "available":
                logger.info("Target instance %s already exists and is available; resuming", instance["id"])
                return run_state  # caller resumes from the current phase without re-provisioning

        return run_state
