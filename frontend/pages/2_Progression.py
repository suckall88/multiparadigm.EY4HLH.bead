"""Progression page: pick an exercise, see the e1RM trend chart and status.

Ez az oldal jeleníti meg a "fejlődés/stagnálás/romlás" témaspecifikus
döntési szabály (backend/domain/progression.py) eredményét grafikonon —
ez teljesíti a beadandó "legalább egy grafikon/statisztikai nézet"
követelményét (nyers JSON-dump helyett).
"""

from __future__ import annotations

import httpx
import pandas as pd
import streamlit as st

from api_client import error_message, get_progression, list_exercises

st.set_page_config(page_title="Progresszió", page_icon="📈")
st.title("📈 Progresszió")

try:
    exercises = list_exercises()
except httpx.HTTPError as exc:
    st.error(f"Nem sikerült lekérni a gyakorlatokat: {error_message(exc)}")
    exercises = []

if not exercises:
    st.info("Nincs még rögzített gyakorlat. Adj hozzá egyet az Edzésnapló oldalon.")
else:
    exercise_options = {f"{e['name']} ({e['category']})": e["id"] for e in exercises}
    chosen = st.selectbox("Gyakorlat", list(exercise_options.keys()))
    # A visszatekintési ablak (hét) paraméterként megy a
    # GET /exercises/{id}/progression?lookback_weeks=... kérésben.
    lookback_weeks = st.slider("Visszatekintés (hét)", min_value=4, max_value=26, value=8)

    exercise_id = exercise_options[chosen]
    try:
        # A tényleges hívás: az api_client a backend/routers/sessions.py
        # get_progression végpontját éri el, ami a ProgressionAnalyzer-en
        # (OOP) és compute_progression-ön (funkcionális domain-szabály)
        # keresztül számolja ki a választ.
        progression = get_progression(exercise_id, lookback_weeks=lookback_weeks)
    except httpx.HTTPError as exc:
        st.error(f"Nem sikerült lekérni a progressziót: {error_message(exc)}")
        progression = None

    if progression is not None:
        # A backend 4 lehetséges státuszt ad vissza (backend/domain/
        # progression.py::ProgressionStatus) — ezekhez itt rendelünk
        # emberi olvasásra szánt címkét és Streamlit-üzenettípust.
        status = progression["status"]
        status_labels = {
            "improving": ("🟢 Fejlődő", "success"),
            "plateau": ("🟡 Stagnálás", "warning"),
            "declining": ("🔴 Visszaesés", "error"),
            "insufficient_data": ("⚪ Kevés adat", "info"),
        }
        label, kind = status_labels.get(status, (status, "info"))
        getattr(st, kind)(label)

        if progression["best_e1rm"] is not None:
            st.metric(
                "Becsült legjobb 1RM",
                f"{progression['best_e1rm']:.1f} kg",
                help=f"Elérve: {progression['best_e1rm_date']}",
            )

        if progression["points"]:
            # A JSON-ból kapott pontokat (dátum, e1RM) pandas
            # DataFrame-mé alakítjuk, a dátumot indexbe tesszük, hogy a
            # Streamlit line_chart-ja idővonalként rajzolja ki.
            df = pd.DataFrame(progression["points"])
            df["session_date"] = pd.to_datetime(df["session_date"])
            df = df.set_index("session_date")
            st.line_chart(df["estimated_one_rm"], y_label="Becsült 1RM (kg)")
        else:
            st.info("Nincs elég adat a diagramhoz.")
