"""Async entrypoint for the scheduled maintenance check.

Run with:
    python -m maintenance.scheduler            # ütemezett ellenőrzés, végtelen ciklusban
    python -m maintenance.scheduler --once     # egyetlen ellenőrzés/csere-ciklus
    python -m maintenance.scheduler --resume   # egy leállt (halted) futás folytatásának jóváhagyása

Uses asyncio.run() as the program's own entry point (not inside an already
running event loop), per Section 6.1. Egy OS-szintű zár biztosítja, hogy
egyszerre csak egy ütemező fusson.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from maintenance.backend_admin import BackendAdmin
from maintenance.config import LOG_DIR, MaintenanceSettings, load_settings
from maintenance.db_swap_controller import DBSwapController
from maintenance.provider_client import RenderClient
from maintenance.state import AnotherSchedulerRunningError, SchedulerLock, StateStore
from maintenance.workflow import resume_halted_run, run_maintenance_cycle

logger = logging.getLogger("maintenance.scheduler")


def _configure_logging() -> None:
    """Konzolra és tartós log-fájlba is ír: minden ellenőrzés és lépés naplózva."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOG_DIR / "scheduler.log", encoding="utf-8"), logging.StreamHandler()],
    )
    # A httpx minden kérést INFO szinten naplózna (URL-ekkel) — ez zaj.
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def run(settings: MaintenanceSettings, once: bool) -> None:
    """Ütemezett ciklus: minden `check_interval_seconds` másodpercben egy
    ellenőrzés (és ha indokolt és jogosított, csere). Egy ciklus váratlan
    hibája nem állítja le az ütemezőt."""
    state_store = StateStore(settings.state_dir)
    logger.info(
        "Maintenance scheduler starting: interval=%ss swap_enabled=%s maintenance_at=%s",
        settings.check_interval_seconds,
        settings.swap_enabled,
        settings.maintenance_at,
    )
    async with (
        RenderClient(settings.render_api_key) as provider,
        BackendAdmin(settings.backend_url, settings.maintenance_token) as admin,
    ):
        controller = DBSwapController(provider, admin, state_store, settings)
        while True:
            try:
                result = await run_maintenance_cycle(settings, provider, controller, state_store)
                logger.info("Cycle finished: %s", result.phase if result else "no swap needed")
            except Exception:
                logger.exception("Unexpected error during maintenance cycle")
            if once:
                return
            await asyncio.sleep(settings.check_interval_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Edzésnapló DB maintenance control program")
    parser.add_argument("--once", action="store_true", help="run a single check/swap cycle and exit")
    parser.add_argument("--resume", action="store_true", help="approve continuing a halted swap run, then exit")
    args = parser.parse_args()

    _configure_logging()
    settings = load_settings()

    lock = SchedulerLock(settings.state_dir)
    try:
        lock.acquire()
    except AnotherSchedulerRunningError as exc:
        logger.error("%s; exiting", exc)
        return 1

    try:
        if args.resume:
            resumed = resume_halted_run(StateStore(settings.state_dir))
            if resumed is None:
                logger.info("No halted run to resume")
            else:
                logger.info("Run %s will continue after step '%s' on the next cycle", resumed.run_id, resumed.phase)
            return 0
        asyncio.run(run(settings, once=args.once))
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
