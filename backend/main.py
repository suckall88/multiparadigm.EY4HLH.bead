"""FastAPI application entrypoint: run with `uvicorn backend.main:app --reload`."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.db import Base, engine
from backend.logging_config import configure_logging
from backend.routers import exercises, internal, sessions

# Ez az egész alkalmazás első futó kódsora: beállítja a naplózást,
# mielőtt bármi más elindulna, hogy induláskor se vesszen el log.
configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI "lifespan" esemény: a `yield` előtti rész induláskor,
    az utána lévő rész leálláskor fut le (itt nincs teendő leálláskor).
    """
    # Dev convenience only: creates tables if they don't exist yet, never drops/recreates.
    # A Base (backend/db.py) ismeri az összes ORM-modellt (backend/models.py),
    # ebből generálja le a CREATE TABLE parancsokat, ha még nem léteznének.
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ensured; API startup complete")
    yield


# `app` az a FastAPI-objektum, amit az uvicorn ténylegesen elindít
# (lásd a start.py-t és a "uvicorn backend.main:app" parancsot).
app = FastAPI(title="Edzésnapló API", lifespan=lifespan)

# Minden router (backend/routers/*.py) a saját végpontjait regisztrálja
# ide — így a main.py maga nem tartalmaz üzleti logikát, csak
# összefűzi a modulokat.
app.include_router(exercises.router)
app.include_router(sessions.router)
app.include_router(internal.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Ha egy végponton belül bárhol el nem kapott kivétel repül fel,
    ez fogja el legvégül: naplózza a hibát (stack trace-szel együtt),
    és a kliens felé egy egységes, biztonságos 500-as JSON választ ad
    — így sosem szivárog ki nyers Python hibaüzenet a Streamlit felé.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
def health() -> dict[str, str]:
    """Egyszerű életjel-végpont — ezt hívja a felhő-szolgáltató
    (Render) és a maintenance modul is, hogy ellenőrizze, fut-e még
    a backend. (Ez az egyetlen "infra" végpont, nem számít bele a
    beadandó ≥3 alkalmazás-végpontos követelményébe.)
    """
    return {"status": "ok"}
