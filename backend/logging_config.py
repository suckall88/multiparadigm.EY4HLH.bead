"""Central logging setup for the backend."""

from __future__ import annotations

import logging

from backend.config import LOG_LEVEL


def configure_logging() -> None:
    """Beállítja a Python beépített `logging` modulját az egész
    folyamatra: a `LOG_LEVEL` (pl. "INFO", "DEBUG") a backend/config.py-ból
    (végső soron a környezeti változókból) jön, a formátum pedig
    időbélyeget, szintet, modulnevet és üzenetet tartalmaz.
    Ezt hívja meg a backend/main.py legelső sorai között, mielőtt
    bármelyik router elkezdene naplózni.
    """
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
