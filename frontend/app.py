"""Streamlit entrypoint: run with `streamlit run frontend/app.py`.

Ez a "Dashboard" (kezdőoldal) — csak áttekintést ad, a tényleges
adatbevitel a bal oldali menüből elérhető aloldalakon történik
(frontend/pages/1_Workout_Log.py, 2_Progression.py).
Ez a fájl SOSEM éri el közvetlenül az adatbázist: minden adatot a
frontend/api_client.py-n keresztül, HTTP-hívással kér le a backendtől.
"""

from __future__ import annotations

import httpx
import streamlit as st

from api_client import error_message, list_sessions

st.set_page_config(page_title="Edzésnapló", page_icon="🏋️", layout="wide")

st.title("🏋️ Edzésnapló")
st.caption("Áttekintés — a részletekhez használd a bal oldali menüt.")

st.subheader("Heti edzések")
try:
    # GET /sessions hívása az api_client-en keresztül; a válasz egy
    # lista dict-ekből (JSON-ból dekódolva), amit a Streamlit
    # táblázatként (st.dataframe) jelenít meg.
    sessions = list_sessions()
    if not sessions:
        st.info("Még nincs rögzített edzés.")
    else:
        st.metric("Rögzített edzésnapok száma", len(sessions))
        st.dataframe(
            [{"Dátum": s["session_date"], "Szettek": len(s["sets"]), "Megjegyzés": s["notes"] or ""} for s in sessions],
            use_container_width=True,
        )
except httpx.HTTPError as exc:
    # Ha a backend nem elérhető vagy hibát ad vissza, ne omoljon
    # össze az oldal — jelenjen meg egy érthető hibaüzenet helyette.
    st.error(f"Nem sikerült lekérni az edzéseket: {error_message(exc)}")
