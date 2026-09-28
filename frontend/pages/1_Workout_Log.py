"""Workout log page: add a session with sets, and filter existing sessions.

Ez a Streamlit oldal valósítja meg a beadandó által elvárt teljes
adatutat: felhasználói bevitel (ez az oldal) -> HTTP kérés
(frontend/api_client.py) -> FastAPI végpont (backend/routers/sessions.py)
-> feldolgozás (backend/services/workout_service.py) -> adatbázis
(backend/models.py) -> válasz vissza ugyanezen az úton -> megjelenítés itt.
"""

from __future__ import annotations

from datetime import date

import httpx
import streamlit as st

from api_client import create_exercise, create_session, list_exercises, list_sessions

st.set_page_config(page_title="Edzésnapló", page_icon="🏋️")
st.title("🏋️ Edzésnapló")

# ---- Add a new exercise (small helper form) --------------------------------

with st.expander("Új gyakorlat felvétele"):
    with st.form("new_exercise_form"):
        ex_name = st.text_input("Gyakorlat neve")
        ex_category = st.selectbox("Kategória", ["push", "pull", "legs", "core", "egyéb"])
        if st.form_submit_button("Létrehozás") and ex_name:
            try:
                # POST /exercises hívása az api_client-en keresztül.
                create_exercise(ex_name, ex_category)
                st.success(f"'{ex_name}' gyakorlat létrehozva.")
                # st.rerun(): újrafuttatja a teljes oldalt, hogy a
                # frissen létrehozott gyakorlat megjelenjen a lenti
                # legördülő listákban is.
                st.rerun()
            except httpx.HTTPStatusError as exc:
                # A backend a hiba részleteit {"detail": "..."} JSON-ban
                # adja vissza (lásd HTTPException a routerekben) — ezt
                # jelenítjük meg közvetlenül a felhasználónak.
                st.error(exc.response.json().get("detail", "Hiba történt."))
            except httpx.HTTPError as exc:
                # Hálózati hiba / időtúllépés / backend el sem érhető.
                st.error(f"Nem sikerült létrehozni a gyakorlatot: {exc}")

# ---- Add a new session ------------------------------------------------------

st.subheader("Új edzés rögzítése")

try:
    exercises = list_exercises()
except httpx.HTTPError as exc:
    st.error(f"Nem sikerült lekérni a gyakorlatokat: {exc}")
    exercises = []

if not exercises:
    st.info("Előbb hozz létre legalább egy gyakorlatot fent.")
else:
    # Legördülőhöz: "Név (kategória)" -> exercise_id leképezés.
    exercise_options = {f"{e['name']} ({e['category']})": e["id"] for e in exercises}

    # A dinamikusan hozzáadható szett-sorok számát a Streamlit
    # session_state-jében tartjuk, mert az oldal minden interakciónál
    # (gombnyomásnál) újrafut, és enélkül elveszne az állapot.
    if "set_rows" not in st.session_state:
        st.session_state.set_rows = 1

    session_date = st.date_input("Dátum", value=date.today())
    notes = st.text_area("Megjegyzés", value="")

    # Minden sorból összeáll egy szett-leíró dict, amit majd egyben
    # küldünk el a POST /sessions kéréssel (beágyazott listaként).
    set_payloads = []
    for i in range(st.session_state.set_rows):
        cols = st.columns(4)
        chosen = cols[0].selectbox("Gyakorlat", list(exercise_options.keys()), key=f"ex_{i}")
        weight = cols[1].number_input("Súly (kg)", min_value=0.0, step=2.5, key=f"w_{i}")
        reps = cols[2].number_input("Ismétlés", min_value=1, step=1, key=f"r_{i}")
        set_payloads.append(
            {
                "exercise_id": exercise_options[chosen],
                "weight_kg": weight,
                "reps": int(reps),
                "set_number": i + 1,
            }
        )

    col_add, col_submit = st.columns(2)
    if col_add.button("+ Új szett sor"):
        st.session_state.set_rows += 1
        st.rerun()

    if col_submit.button("Edzés mentése", type="primary"):
        # Csak a ténylegesen kitöltött (súly > 0) sorokat küldjük el —
        # az üres sorokat a felhasználó egyszerűen otthagyhatja.
        valid_sets = [s for s in set_payloads if s["weight_kg"] > 0]
        if not valid_sets:
            st.warning("Adj meg legalább egy érvényes szettet (súly > 0).")
        else:
            try:
                # Ez a hívás indítja el a teljes adatutat: POST /sessions
                # -> workout_service.create_workout_session_with_sets ->
                # adatbázis-írás -> a válasz a mentett session-t adja vissza.
                create_session(session_date, notes or None, valid_sets)
                st.success("Edzés elmentve.")
                st.session_state.set_rows = 1
                st.rerun()
            except httpx.HTTPStatusError as exc:
                st.error(exc.response.json().get("detail", "Hiba történt a mentés során."))
            except httpx.HTTPError as exc:
                st.error(f"Nem sikerült elmenteni az edzést: {exc}")

# ---- Filter & list existing sessions ---------------------------------------

st.subheader("Korábbi edzések szűrése")
col_start, col_end = st.columns(2)
start = col_start.date_input("Kezdő dátum", value=None, key="filter_start")
end = col_end.date_input("Záró dátum", value=None, key="filter_end")

try:
    # GET /sessions?start_date=...&end_date=... — ez a "valós
    # felhasználói művelet" (szűrés), amit a beadandó megkövetel.
    sessions = list_sessions(start_date=start or None, end_date=end or None)
    if not sessions:
        st.info("Nincs a szűrésnek megfelelő edzés.")
    else:
        for s in sessions:
            with st.expander(f"{s['session_date']} — {len(s['sets'])} szett"):
                if s["notes"]:
                    st.write(s["notes"])
                st.table(
                    [{"Gyakorlat ID": row["exercise_id"], "Súly": row["weight_kg"], "Ismétlés": row["reps"]} for row in s["sets"]]
                )
except httpx.HTTPError as exc:
    st.error(f"Nem sikerült lekérni az edzéseket: {exc}")
