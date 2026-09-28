"""Async entrypoint for the scheduled maintenance check.

Run with: python -m maintenance.scheduler
Uses asyncio.run() as the program's own entry point (not inside an already
running event loop), per Section 6.1.

Ez a fájl a maintenance vezérlőprogram önálló belépési pontja — a
beadandó Section 6.1 elvárása szerint a maintenance program a
backendtől ÉS a frontendtől független, saját futó program.
"""

from __future__ import annotations

import asyncio
import logging

from maintenance.config import CHECK_INTERVAL_SECONDS, LOG_DIR
from maintenance.workflow import run_maintenance_cycle

# Saját log-fájlba ÉS a konzolra is ír, hogy a beadandó Section 6.2
# elvárása szerint minden ellenőrzés/lépés naplózva legyen, tartósan.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "scheduler.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


async def main(active_instance_id: str, source_dsn: str) -> None:
    """Végtelen ciklus: minden `CHECK_INTERVAL_SECONDS` másodpercben
    lefuttat egy teljes ellenőrzés-és-esetleg-csere ciklust
    (maintenance/workflow.py::run_maintenance_cycle). Egy ciklusban
    fellépő VÁRATLAN kivételt (bármi, amit a workflow nem kezelt le
    saját maga) itt fogunk el, hogy egyetlen hibás ciklus se állítsa
    le a teljes ütemezőt — a következő ciklus úgyis újrapróbálja."""
    logger.info("Maintenance scheduler starting; interval=%ss", CHECK_INTERVAL_SECONDS)
    while True:
        try:
            run_state = await run_maintenance_cycle(active_instance_id, source_dsn)
            logger.info("Cycle complete: phase=%s", run_state.phase)
        except Exception:
            logger.exception("Unexpected error during maintenance cycle")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    import os

    # A célinstance azonosítóját és a forrás DSN-t környezeti
    # változóból olvassuk be — sosem drótozzuk be a kódba (lásd
    # Section 5.4: nincs hardkódolt kapcsolati adat).
    instance_id = os.getenv("ACTIVE_DB_INSTANCE_ID", "")
    dsn = os.getenv("DATABASE_URL", "")
    if not instance_id or not dsn:
        logger.warning(
            "ACTIVE_DB_INSTANCE_ID or DATABASE_URL not set; scheduler will keep checking "
            "but every cycle will report an unreachable/misconfigured source."
        )
    # `asyncio.run()` itt, a modul saját belépési pontjaként indítja el
    # az eseményhurkot — nem egy már futó event loop-on belülről hívjuk,
    # ahogy a Section 6.1 megköveteli.
    asyncio.run(main(instance_id, dsn))
