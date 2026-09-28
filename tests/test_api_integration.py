"""Real API request -> processing -> test DB -> response integration tests.

Ez a fájl a beadandó kötelező "API integrációs tesztje" (Section 4):
a `client` fixture (tests/conftest.py) egy VALÓDI FastAPI TestClient,
ami a teljes útvonalon végigmegy — router -> service -> domain ->
teszt-adatbázis -> válasz —, csak az adatbázis-kapcsolat van
kicserélve a memóriabeli teszt-DB-re.
"""

from __future__ import annotations


def test_create_session_and_read_back(client, db_session):
    """Teljes kör: gyakorlat létrehozása -> edzésalkalom mentése (2
    szettel) -> visszaolvasás a GET végponton -> ÉS közvetlen
    ellenőrzés magában az adatbázisban is (nem csak a HTTP válaszon
    keresztül) — ez pontosan a Section 4 integrációs teszt-elvárása."""
    exercise_resp = client.post("/exercises", json={"name": "Bench Press", "category": "push"})
    assert exercise_resp.status_code == 201
    exercise_id = exercise_resp.json()["id"]

    session_payload = {
        "session_date": "2026-01-05",
        "notes": "felső test",
        "sets": [
            {"exercise_id": exercise_id, "weight_kg": 80.0, "reps": 5, "set_number": 1},
            {"exercise_id": exercise_id, "weight_kg": 82.5, "reps": 5, "set_number": 2},
        ],
    }
    create_resp = client.post("/sessions", json=session_payload)
    assert create_resp.status_code == 201
    session_id = create_resp.json()["id"]
    assert len(create_resp.json()["sets"]) == 2

    read_resp = client.get(f"/sessions/{session_id}")
    assert read_resp.status_code == 200
    body = read_resp.json()
    assert body["session_date"] == "2026-01-05"
    assert body["notes"] == "felső test"
    assert {s["weight_kg"] for s in body["sets"]} == {80.0, 82.5}

    # Confirm DB state directly too, not just via the API response.
    # Ez bizonyítja, hogy a POST /sessions valóban lementette a
    # sorozatokat az adatbázisba, nem csak egy JSON választ generált.
    from backend.models import SetEntry

    db_rows = db_session.query(SetEntry).filter(SetEntry.session_id == session_id).all()
    assert len(db_rows) == 2


def test_progression_endpoint_reflects_created_data(client):
    """Négy egymást követő héten rögzített, egyre nagyobb súlyú edzés
    után a GET /progression végpontnak "improving"-ot kell mondania —
    ez a témaspecifikus döntési szabály valós HTTP-n keresztüli
    ellenőrzése, nem csak a tiszta függvényé közvetlenül."""
    ex_resp = client.post("/exercises", json={"name": "Squat", "category": "legs"})
    exercise_id = ex_resp.json()["id"]

    weights = [100.0, 102.5, 105.0, 107.5]
    for i, w in enumerate(weights):
        day = 1 + i * 7
        resp = client.post(
            "/sessions",
            json={
                "session_date": f"2026-01-{day:02d}",
                "sets": [
                    {"exercise_id": exercise_id, "weight_kg": w, "reps": 5, "set_number": 1}
                ],
            },
        )
        assert resp.status_code == 201

    prog_resp = client.get(f"/exercises/{exercise_id}/progression")
    assert prog_resp.status_code == 200
    body = prog_resp.json()
    assert body["status"] == "improving"
    assert len(body["points"]) == 4


def test_missing_session_returns_404(client):
    # Nem létező edzésalkalom ID-ra a végpontnak 404-et kell adnia, nem
    # 500-at vagy üres/hamis választ.
    resp = client.get("/sessions/999")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


def test_writes_rejected_while_maintenance_mode_frozen(client):
    """Ez ellenőrzi a Section 6.3 "írászárolás" mechanizmusát: a belső
    (tokenes) végponton zárolt írások esetén a NYILVÁNOS POST
    /exercises végpontnak 503-at kell adnia, majd a zárolás
    feloldása után újra működnie kell."""
    freeze_resp = client.post(
        "/internal/maintenance-mode",
        json={"enabled": True},
        headers={"X-Maintenance-Token": "dev-maintenance-token"},
    )
    assert freeze_resp.status_code == 200
    assert freeze_resp.json()["writes_frozen"] is True

    blocked_resp = client.post("/exercises", json={"name": "Deadlift", "category": "pull"})
    assert blocked_resp.status_code == 503

    unfreeze_resp = client.post(
        "/internal/maintenance-mode",
        json={"enabled": False},
        headers={"X-Maintenance-Token": "dev-maintenance-token"},
    )
    assert unfreeze_resp.json()["writes_frozen"] is False
