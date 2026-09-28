"""Stateful controller executing the swap steps from the assignment spec (Section 6.3).

OOP paradigm: DBSwapController owns its collaborators (Render client,
backend admin, state store, backup operations) and exposes one method per
swap step, each of which persists the run state before returning.

Minden lépés úgy készült, hogy megszakadás után biztonságosan
megismételhető legyen (a törlés 404-et elfogad, a létrehozás előbb név
szerint keres, a visszaállítás --clean módban fut), így a workflow
mindig az utoljára befejezett lépés UTÁNI lépéstől folytathatja.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from maintenance.backend_admin import BackendAdmin
from maintenance.backup import BackupMetadata, backup_database, load_backup, missing_pg_tools, restore_database
from maintenance.config import MaintenanceSettings
from maintenance.models import RunState
from maintenance.provider_client import AmbiguousRenderError, RenderApiError, RenderClient, RenderNotFoundError
from maintenance.rehearsal import rehearse_restore
from maintenance.retry import Sleep
from maintenance.snapshot import compare_snapshots
from maintenance.state import StateStore

logger = logging.getLogger(__name__)

Step = Callable[[RunState], Awaitable[RunState]]


class SwapHaltedError(RuntimeError):
    """The controller cannot safely continue; a human must follow up."""


@dataclass(frozen=True)
class SwapOps:
    """A külső eszközöket (pg_dump/pg_restore) hívó műveletek — a tesztek
    ezeket cserélik le hamis változatokra, a vezérlési logikát nem."""

    backup: Callable[[str, str, dict[str, Any], Path], BackupMetadata] = backup_database
    load_backup: Callable[[str | None], BackupMetadata] = load_backup
    restore: Callable[[BackupMetadata, str], None] = restore_database
    rehearse: Callable[[BackupMetadata, str], Awaitable[None]] = rehearse_restore
    missing_tools: Callable[[], list[str]] = missing_pg_tools


class DBSwapController:
    def __init__(
        self,
        provider: RenderClient,
        admin: BackendAdmin,
        state_store: StateStore,
        settings: MaintenanceSettings,
        ops: SwapOps | None = None,
        sleep: Sleep = asyncio.sleep,
    ):
        self._provider = provider
        self._admin = admin
        self._state = state_store
        self._settings = settings
        self._ops = ops or SwapOps()
        self._sleep = sleep

    # ---- run-state bookkeeping ---------------------------------------------------

    def _advance(self, run_state: RunState, phase: str, **updates: object) -> RunState:
        """Egy lépés sikeres befejezésének tartós rögzítése (és naplózása)."""
        run_state.phase = phase  # type: ignore[assignment]
        run_state.updated_at = datetime.now(timezone.utc)
        for key, value in updates.items():
            setattr(run_state, key, value)
        run_state.log.append(f"{run_state.updated_at.isoformat()} {phase}")
        self._state.save(run_state)
        logger.info("Swap run %s: step '%s' done", run_state.run_id, phase)
        return run_state

    def halt(self, run_state: RunState, reason: str) -> RunState:
        """Biztonságos leállás: a befejezett lépés (`halted_from`) megmarad,
        így kézi ellenőrzés után a futás onnan folytatható."""
        logger.error("Swap run %s halted after '%s': %s", run_state.run_id, run_state.phase, reason)
        return self._advance(run_state, "halted", halted_from=run_state.phase, error=reason)

    def steps(self) -> list[tuple[str, Step]]:
        """A lépések sorrendben, mindegyik azzal a fázissal, amit befejezve rögzít."""
        return [
            ("prerequisites_ok", self.check_prerequisites),
            ("writes_frozen", self.freeze_writes),
            ("backed_up", self.backup_source),
            ("rehearsal_verified", self.rehearse_restore),
            ("old_instance_deleted", self.delete_old_instance),
            ("target_provisioned", self.provision_target),
            ("restored", self.restore_target),
            ("repointed", self.repoint_backend),
            ("verified", self.verify_cutover),
            ("completed", self.release_writes),
        ]

    # ---- 1. prerequisites ------------------------------------------------------------

    async def check_prerequisites(self, run_state: RunState) -> RunState:
        """Konfiguráció, eszközök, mentési hely, jogosultság és erőforrás-azonosítók."""
        s = self._settings
        required = {
            "RENDER_API_KEY": s.render_api_key,
            "RENDER_OWNER_ID": s.render_owner_id,
            "RENDER_WEB_SERVICE_ID": s.render_web_service_id,
            "REHEARSAL_DATABASE_URL": s.rehearsal_database_url,
            "ACTIVE_DB_INSTANCE_ID": run_state.active_instance_id,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise SwapHaltedError(f"Missing configuration: {', '.join(missing)}")
        if tools := self._ops.missing_tools():
            raise SwapHaltedError(f"Backup tools not found on PATH: {', '.join(tools)}")
        s.backup_dir.mkdir(parents=True, exist_ok=True)
        probe = s.backup_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()

        # Ezek a hívások a jogosultságot is igazolják (401/403 -> leállás).
        await self._provider.get_service(s.render_web_service_id)
        instance = await self._provider.get_postgres(run_state.active_instance_id or "")
        owner_id = (instance.get("owner") or {}).get("id")
        if owner_id != s.render_owner_id:
            raise SwapHaltedError(
                f"Instance {run_state.active_instance_id} is not owned by the configured workspace; refusing to manage it"
            )
        logger.info(
            "Prerequisites OK; new instance will use plan=%s region=%s version=%s",
            s.target_plan,
            s.target_region,
            s.target_pg_version,
        )
        return self._advance(run_state, "prerequisites_ok")

    # ---- 2. stop writes, then final backup ------------------------------------------------

    async def freeze_writes(self, run_state: RunState) -> RunState:
        """Írászárolás KÉT szinten: a futó backend memóriájában azonnal, és
        a Render WRITES_FROZEN környezeti változójában, hogy a zár egy
        újraindulást/redeployt is túléljen. Utána kivárjuk a már futó
        írások lezárulását, és csak ezután jöhet a mentés."""
        await self._provider.set_env_var(self._settings.render_web_service_id, "WRITES_FROZEN", "true")
        if not await self._admin.set_writes_frozen(True):
            raise SwapHaltedError("Backend did not confirm the write freeze")
        await self._sleep(self._settings.write_drain_seconds)
        return self._advance(run_state, "writes_frozen")

    async def backup_source(self, run_state: RunState) -> RunState:
        """Tartalmi pillanatkép a backendről + pg_dump a forrás példányról."""
        source_id = run_state.active_instance_id or ""
        source_dsn = await self._provider.get_connection_string(source_id)
        snapshot = await self._admin.fetch_snapshot()
        metadata = await asyncio.to_thread(self._ops.backup, source_dsn, source_id, snapshot, self._settings.backup_dir)
        return self._advance(run_state, "backed_up", backup_id=metadata.metadata_path)

    # ---- 3. practice restore, then retire the old instance (1 free instance limit) --------

    async def rehearse_restore(self, run_state: RunState) -> RunState:
        metadata = self._ops.load_backup(run_state.backup_id)
        await self._ops.rehearse(metadata, self._settings.rehearsal_database_url)
        return self._advance(run_state, "rehearsal_verified")

    async def delete_old_instance(self, run_state: RunState) -> RunState:
        """Csak az ellenőrzött próbavisszaállítás UTÁN fut; 404 esetén (már
        törölve egy korábbi, megszakadt futásban) is továbbmegy."""
        await self._provider.delete_postgres(run_state.active_instance_id or "")
        return self._advance(run_state, "old_instance_deleted")

    # ---- 4. create the new instance and restore into it -------------------------------------

    def _target_name(self, run_state: RunState) -> str:
        # A név a futás azonosítójából képződik, így egy megszakadt vagy
        # bizonytalan kimenetelű létrehozás után név szerint megtalálható.
        return f"edzesnaplo-db-{run_state.run_id[:8]}"

    async def provision_target(self, run_state: RunState) -> RunState:
        s = self._settings
        name = self._target_name(run_state)
        instance = await self._provider.find_postgres_by_name(name, s.render_owner_id)
        if instance is not None:
            logger.info("Target instance %s (%s) already exists; reusing it", instance["id"], name)
        else:
            try:
                instance = await self._provider.create_postgres(
                    name=name,
                    owner_id=s.render_owner_id,
                    plan=s.target_plan,
                    region=s.target_region,
                    version=s.target_pg_version,
                )
            except AmbiguousRenderError:
                instance = await self._provider.find_postgres_by_name(name, s.render_owner_id)
                if instance is None:
                    raise SwapHaltedError(f"Creating {name} had an unknown outcome and it cannot be found")
        return self._advance(run_state, "target_provisioned", target_instance_id=instance["id"])

    async def restore_target(self, run_state: RunState) -> RunState:
        metadata = self._ops.load_backup(run_state.backup_id)
        target_id = run_state.target_instance_id or ""
        await self._provider.wait_until_available(target_id)
        target_dsn = await self._provider.get_connection_string(target_id)
        await asyncio.to_thread(self._ops.restore, metadata, target_dsn)
        return self._advance(run_state, "restored")

    # ---- 5. repoint the backend, verify, record, release ------------------------------------

    async def repoint_backend(self, run_state: RunState) -> RunState:
        """Csak a DATABASE_URL változik (a többi beállítás marad); a
        WRITES_FROZEN=true miatt az újraindult backend zárolt írással indul."""
        service_id = self._settings.render_web_service_id
        target_dsn = await self._provider.get_connection_string(run_state.target_instance_id or "")
        await self._provider.set_env_var(service_id, "DATABASE_URL", target_dsn)
        deploy_id = await self._provider.trigger_deploy(service_id)
        await self._provider.wait_until_deploy_live(service_id, deploy_id)
        return self._advance(run_state, "repointed")

    async def verify_cutover(self, run_state: RunState) -> RunState:
        """A backend olvasó végpontjai a mentéskori tartalmat adják-e vissza,
        és írható-e az új adatbázis. Siker esetén az új aktív példányt és a
        teljesített kérést TARTÓSAN rögzítjük, még az írások feloldása előtt."""
        metadata = self._ops.load_backup(run_state.backup_id)
        differences = compare_snapshots(metadata.snapshot, await self._admin.fetch_snapshot())
        if differences:
            raise SwapHaltedError("Restored content differs: " + "; ".join(differences))
        if not await self._admin.verify_writable():
            raise SwapHaltedError("Post-swap write check failed")
        return self._advance(
            run_state,
            "verified",
            active_instance_id=run_state.target_instance_id,
            target_instance_id=None,
            fulfilled_trigger=run_state.trigger_key,
        )

    async def release_writes(self, run_state: RunState) -> RunState:
        await self._provider.set_env_var(self._settings.render_web_service_id, "WRITES_FROZEN", "false")
        if await self._admin.set_writes_frozen(False):
            raise SwapHaltedError("Backend did not confirm releasing the write freeze")
        return self._advance(run_state, "completed", error=None)

    # ---- resume-after-interruption reconciliation --------------------------------------------

    async def reconcile(self, run_state: RunState) -> RunState:
        """Folytatás előtt egyeztet a tényleges állapottal, sosem tippel:
        a rögzített mentésnek épnek kell lennie, a rögzített célpéldánynak
        léteznie kell a szolgáltatónál. Ha ez nem igazolható, leáll."""
        if run_state.backup_id and run_state.phase not in ("verified", "completed"):
            self._ops.load_backup(run_state.backup_id)  # NoBackupAvailableError -> halt
        if run_state.target_instance_id:
            try:
                await self._provider.get_postgres(run_state.target_instance_id)
            except RenderNotFoundError:
                return self.halt(run_state, f"Recorded target instance {run_state.target_instance_id} no longer exists")
            except (RenderApiError, httpx.HTTPError) as exc:
                return self.halt(run_state, f"Could not verify target instance state on resume: {exc}")
        logger.info("Reconciled interrupted run %s at phase '%s'", run_state.run_id, run_state.phase)
        return run_state
