"""Frontend configuration: backend URL, read from the environment or Streamlit secrets.

Ez adja meg, HOVA küldje a Streamlit a kéréseket. Sorrend:
1. `BACKEND_URL` környezeti változó (lokálisan a .env / a shell; a
   Streamlit Community Cloud a gyökérszintű secreteket környezeti
   változóként is elérhetővé teszi),
2. `st.secrets["BACKEND_URL"]`, de CSAK ha létezik secrets.toml — ha
   nincs, az st.secrets elérése egy "No secrets found" hibát rajzolna
   az oldalra, ami után a set_page_config már nem hívható,
3. végső esetben a helyi fejlesztői backend (localhost:8000).
Ezt importálja a frontend/api_client.py minden HTTP-híváshoz.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_BACKEND_URL = "http://localhost:8000"


def _secrets_file_exists() -> bool:
    candidates = (Path.home() / ".streamlit" / "secrets.toml", Path.cwd() / ".streamlit" / "secrets.toml")
    return any(path.exists() for path in candidates)


def _backend_url() -> str:
    if url := os.getenv("BACKEND_URL"):
        return url
    if _secrets_file_exists():
        import streamlit as st

        return str(st.secrets.get("BACKEND_URL", DEFAULT_BACKEND_URL))
    return DEFAULT_BACKEND_URL


BACKEND_URL: str = _backend_url().rstrip("/")
