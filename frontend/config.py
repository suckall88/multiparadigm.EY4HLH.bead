"""Frontend configuration: backend URL, read from Streamlit secrets or env.

Ez adja meg, HOVA küldje a Streamlit a kéréseket. Elsőként a Streamlit
Cloud "secrets" tárolóját nézi (st.secrets — ezt a felhős admin
felületen állítja be a felhasználó, sosem a kódban), ha az nem elérhető
(pl. lokális futtatásnál), akkor a `BACKEND_URL` környezeti változót,
végső esetben a helyi fejlesztői backend címét (localhost:8000).
Ezt importálja be a frontend/api_client.py minden HTTP-híváshoz.
"""

from __future__ import annotations

import os

try:
    import streamlit as st

    BACKEND_URL: str = st.secrets.get("BACKEND_URL", os.getenv("BACKEND_URL", "http://localhost:8000"))
except Exception:
    # Ha valamiért nem fut Streamlit-kontextusban (pl. egy sima Python
    # szkriptből importálják tesztelés céljából), essünk vissza a
    # környezeti változóra / alapértékre.
    BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
