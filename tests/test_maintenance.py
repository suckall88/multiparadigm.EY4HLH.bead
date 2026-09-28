"""Unit tests for the maintenance program's decisions and swap control.

Nothing here touches a live service or a real cloud resource: the Render
API is replaced either by an in-memory fake (`FakeProvider`) or by an
httpx.MockTransport, the backend by `FakeAdmin`, and pg_dump/pg_restore
by recording fakes. The workflow and controller code under test is the
real one.

Lefedett esetek (Section 4): átmeneti szolgáltatáshiba, hiányzó/sérült
mentés, megszakadt futás folytatása duplikáció nélkül, teljesített kérés
újra nem indít cserét, és a korlátozott újrapróbálkozás szabályai.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from maintenance.backup import BackupMetadata, NoBackupAvailableError, load_backup
from maintenance.config import MaintenanceSettings
from maintenance.db_swap_controller import DBSwapController, SwapOps
from maintenance.decision import decide_swap, parse_maintenance_at
from maintenance.models import CheckResult, RunState
from maintenance.provider_client import (
    AmbiguousRenderError,
    PermissionOrQuotaError,
    RenderClient,
    RenderNotFoundError,
    TransientRenderError,
)
from maintenance.snapshot import compare_snapshots
from maintenance.state import StateStore
from maintenance.workflow import run_maintenance_cycle

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
BUDAPEST = ZoneInfo("Europe/Budapest")

SNAPSHOT: dict[str, Any] = {
    "exercises": [{"id": 1, "name": "Guggolás", "category": "legs"}],
    "sessions": [{"id": 1, "session_date": "2026-09-01", "sets": [{"id": 1, "exercise_id": 1, "weight_kg": 100.0}]}],
    "progression": {"1": {"exercise_id": 1, "status": "insufficient_data", "best_e1rm": 110.0}},
}


def _run_state(phase: str = "completed", **overrides: Any) -> RunState:
    defaults: dict[str, Any] = dict(run_id="run-1234abcd", phase=phase, started_at=NOW, updated_at=NOW)
    defaults.update(overrides)
    return RunState(**defaults)


# ---- decide_swap (pure) --------------------------------------------------------------


def test_decide_swap_transient_failure_no_swap():
    # Puszta elérhetetlenség közelgő lejárat mellett sem indokol cserét.
    check = CheckResult(checked_at=NOW, reachable=False, error="timeout")
    decision = decide_swap(check, None, NOW, maintenance_at=NOW - timedelta(hours=1))
    assert decision.should_swap is False
    assert "unreachable" in decision.reason


def test_decide_swap_approaching_expiry_triggers_swap():
    expires = NOW + timedelta(days=2)
    decision = decide_swap(CheckResult(checked_at=NOW, reachable=True, expires_at=expires), None, NOW)
    assert decision.should_swap is True
    assert decision.trigger_key == f"expiry:{expires.isoformat()}"


def test_decide_swap_far_from_expiry_no_swap():
    check = CheckResult(checked_at=NOW, reachable=True, expires_at=NOW + timedelta(days=20))
    assert decide_swap(check, None, NOW).should_swap is False


@pytest.mark.parametrize("kind", ["expiry", "maintenance_at"])
def test_decide_swap_fulfilled_request_not_retriggered(kind):
    # A legutóbbi sikeres futás már teljesítette pontosan ezt a kérést.
    due = NOW - timedelta(hours=1)
    expires = NOW + timedelta(days=1)
    if kind == "expiry":
        check = CheckResult(checked_at=NOW, reachable=True, expires_at=expires)
        last = _run_state(fulfilled_trigger=f"expiry:{expires.isoformat()}")
        decision = decide_swap(check, last, NOW)
    else:
        check = CheckResult(checked_at=NOW, reachable=True, expires_at=NOW + timedelta(days=25))
        last = _run_state(fulfilled_trigger=f"maintenance_at:{due.isoformat()}")
        decision = decide_swap(check, last, NOW, maintenance_at=due)
    assert decision.should_swap is False
    assert decision.reason == "maintenance request already fulfilled"


@pytest.mark.parametrize(
    ("raw", "expected_utc"),
    [
        ("", None),
        ("2026-10-01T14:00", datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)),  # CEST, UTC+2
        ("2026-12-01T14:00", datetime(2026, 12, 1, 13, 0, tzinfo=timezone.utc)),  # CET, UTC+1
        ("2026-10-01T14:00+00:00", datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)),
    ],
)
def test_parse_maintenance_at_uses_configured_timezone(raw, expected_utc):
    assert parse_maintenance_at(raw, BUDAPEST) == expected_utc


# ---- snapshot comparison (pure) --------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda s: None, []),
        (lambda s: s["exercises"].clear(), ["exercises: record id=1 missing"]),
        (lambda s: s["sessions"][0]["sets"][0].update(weight_kg=90.0), ["sessions: record id=1 differs"]),
        (lambda s: s["progression"]["1"].update(status="improving"), ["progression: exercise 1 result differs"]),
    ],
)
def test_compare_snapshots(mutate, expected):
    actual = json.loads(json.dumps(SNAPSHOT))
    mutate(actual)
    assert compare_snapshots(SNAPSHOT, actual) == expected


# ---- backup integrity ---------------------------------------------------------------------


def test_load_backup_rejects_missing_or_corrupt_backup(tmp_path: Path):
    with pytest.raises(NoBackupAvailableError):
        load_backup(None)
    with pytest.raises(NoBackupAvailableError):
        load_backup(str(tmp_path / "nothing.json"))

    dump = tmp_path / "src_1.dump"
    dump.write_bytes(b"original")
    meta = BackupMetadata("src", NOW.isoformat(), str(dump), "0" * 64, SNAPSHOT)
    Path(meta.metadata_path).write_text(json.dumps(meta.__dict__), encoding="utf-8")
    with pytest.raises(NoBackupAvailableError, match="checksum"):
        load_backup(meta.metadata_path)


# ---- workflow + controller with fakes ---------------------------------------------------------


class FakeProvider:
    """In-memory Render: példányok név/ID szerint, és minden hívás naplózva."""

    def __init__(self) -> None:
        self.instances: dict[str, dict[str, Any]] = {
            "db-old": {"id": "db-old", "name": "edzesnaplo-db", "status": "available", "owner": {"id": "own-1"}},
        }
        self.calls: list[str] = []
        self._created = 0

    async def get_postgres(self, instance_id: str) -> dict[str, Any]:
        if instance_id not in self.instances:
            raise RenderNotFoundError(instance_id)
        return self.instances[instance_id]

    async def get_connection_string(self, instance_id: str) -> str:
        return f"postgresql://user:secret@{instance_id}.example/db"

    async def find_postgres_by_name(self, name: str, owner_id: str) -> dict[str, Any] | None:
        return next((i for i in self.instances.values() if i["name"] == name), None)

    async def create_postgres(self, *, name: str, **_: Any) -> dict[str, Any]:
        self.calls.append("create")
        self._created += 1
        instance = {"id": f"db-{self._created}", "name": name, "status": "available", "owner": {"id": "own-1"}}
        self.instances[instance["id"]] = instance
        return instance

    async def delete_postgres(self, instance_id: str) -> None:
        self.calls.append(f"delete:{instance_id}")
        self.instances.pop(instance_id, None)

    async def wait_until_available(self, instance_id: str) -> dict[str, Any]:
        return await self.get_postgres(instance_id)

    async def get_service(self, service_id: str) -> dict[str, Any]:
        return {"id": service_id}

    async def set_env_var(self, service_id: str, key: str, value: str) -> None:
        self.calls.append(f"env:{key}={'<dsn>' if key == 'DATABASE_URL' else value}")

    async def trigger_deploy(self, service_id: str) -> str:
        self.calls.append("deploy")
        return "dep-1"

    async def wait_until_deploy_live(self, service_id: str, deploy_id: str) -> None:
        return None


class FakeAdmin:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.snapshot = json.loads(json.dumps(SNAPSHOT))

    async def set_writes_frozen(self, frozen: bool) -> bool:
        self.calls.append(f"freeze:{frozen}")
        return frozen

    async def fetch_snapshot(self) -> dict[str, Any]:
        self.calls.append("snapshot")
        return self.snapshot

    async def verify_writable(self) -> bool:
        self.calls.append("write_check")
        return True


def _settings(tmp_path: Path, **overrides: Any) -> MaintenanceSettings:
    settings = MaintenanceSettings(
        backend_url="http://backend.test",
        maintenance_token="t",
        render_api_key="k",
        render_owner_id="own-1",
        render_web_service_id="srv-1",
        active_db_instance_id="db-old",
        rehearsal_database_url="postgresql://localhost/rehearsal",
        swap_enabled=True,
        check_interval_seconds=60,
        expiry_warning_days=3,
        maintenance_at=NOW - timedelta(hours=1),
        timezone=BUDAPEST,
        target_plan="free",
        target_region="frankfurt",
        target_pg_version="16",
        write_drain_seconds=0,
        backup_dir=tmp_path / "backups",
        state_dir=tmp_path,
    )
    return replace(settings, **overrides)


class Harness:
    """A valódi workflow + DBSwapController, hamis külső függőségekkel."""

    def __init__(self, tmp_path: Path, **setting_overrides: Any) -> None:
        self.settings = _settings(tmp_path, **setting_overrides)
        self.provider = FakeProvider()
        self.admin = FakeAdmin(self.provider.calls)
        self.store = StateStore(tmp_path)
        self.calls = self.provider.calls
        self.backups: dict[str, BackupMetadata] = {}

        def backup(dsn: str, source_id: str, snapshot: dict[str, Any], backup_dir: Path) -> BackupMetadata:
            self.calls.append("backup")
            meta = BackupMetadata(source_id, NOW.isoformat(), str(backup_dir / "b.dump"), "x", snapshot)
            self.backups[meta.metadata_path] = meta
            return meta

        def load(metadata_path: str | None) -> BackupMetadata:
            if metadata_path not in self.backups:
                raise NoBackupAvailableError(f"missing {metadata_path}")
            return self.backups[metadata_path]

        async def rehearse(meta: BackupMetadata, dsn: str) -> None:
            self.calls.append("rehearse")

        ops = SwapOps(
            backup=backup,
            load_backup=load,
            restore=lambda meta, dsn: self.calls.append(f"restore:{dsn.split('@')[1].split('.')[0]}"),
            rehearse=rehearse,
            missing_tools=lambda: [],
        )

        async def no_sleep(_seconds: float) -> None:
            return None

        self.controller = DBSwapController(self.provider, self.admin, self.store, self.settings, ops, sleep=no_sleep)

    async def cycle(self) -> RunState | None:
        return await run_maintenance_cycle(self.settings, self.provider, self.controller, self.store, now=NOW)


@pytest.mark.asyncio
async def test_full_swap_runs_steps_in_safe_order_and_is_not_retriggered(tmp_path: Path):
    h = Harness(tmp_path)

    result = await h.cycle()

    assert result is not None and result.phase == "completed"
    assert result.active_instance_id == "db-1"
    assert h.calls == [
        "env:WRITES_FROZEN=true",  # a zár túléli az újraindulást
        "freeze:True",
        "snapshot",
        "backup",  # a mentés csak a zárolás után
        "rehearse",  # próbavisszaállítás ...
        "delete:db-old",  # ... és csak utána törlődik a régi példány
        "create",
        "restore:db-1",
        "env:DATABASE_URL=<dsn>",
        "deploy",
        "snapshot",  # visszaállított tartalom ellenőrzése
        "write_check",
        "env:WRITES_FROZEN=false",
        "freeze:False",
    ]
    assert h.store.load().fulfilled_trigger == result.trigger_key

    # Ugyanaz a MAINTENANCE_AT a következő ciklusban már nem indít cserét.
    h.calls.clear()
    assert await h.cycle() is None
    assert h.calls == []


@pytest.mark.asyncio
async def test_swap_not_started_without_authorization(tmp_path: Path):
    h = Harness(tmp_path, swap_enabled=False)
    assert await h.cycle() is None
    assert h.calls == []


@pytest.mark.asyncio
async def test_resume_interrupted_run_continues_without_duplicates(tmp_path: Path):
    """A program a régi példány törlése után, az új létrehozásának
    rögzítése előtt állt le (a Rendernél a példány már létrejött)."""
    h = Harness(tmp_path)
    meta = BackupMetadata("db-old", NOW.isoformat(), "b.dump", "x", SNAPSHOT)
    h.backups[meta.metadata_path] = meta
    h.provider.instances.pop("db-old")
    h.provider.instances["db-9"] = {"id": "db-9", "name": "edzesnaplo-db-run-1234", "status": "available"}
    h.store.save(
        _run_state("old_instance_deleted", active_instance_id="db-old", backup_id=meta.metadata_path, trigger_key="k")
    )

    result = await h.cycle()

    assert result is not None and result.phase == "completed"
    assert result.active_instance_id == "db-9"  # a már létező példányt vette át
    assert "create" not in h.calls
    assert "backup" not in h.calls and not any(c.startswith("delete") for c in h.calls)


@pytest.mark.asyncio
async def test_resume_halts_when_recorded_target_missing_and_stays_halted(tmp_path: Path):
    h = Harness(tmp_path)
    meta = BackupMetadata("db-old", NOW.isoformat(), "b.dump", "x", SNAPSHOT)
    h.backups[meta.metadata_path] = meta
    h.store.save(_run_state("target_provisioned", backup_id=meta.metadata_path, target_instance_id="db-gone"))

    result = await h.cycle()

    assert result is not None and result.phase == "halted"
    assert result.halted_from == "target_provisioned"
    assert "create" not in h.calls
    # Egy leállt futás után a következő ciklus sem nyúl semmihez.
    h.calls.clear()
    assert (await h.cycle()).phase == "halted"
    assert h.calls == []


@pytest.mark.asyncio
async def test_missing_backup_halts_before_old_instance_is_deleted(tmp_path: Path):
    h = Harness(tmp_path)
    h.store.save(_run_state("backed_up", active_instance_id="db-old", backup_id="lost.json"))

    result = await h.cycle()

    assert result is not None and result.phase == "halted"
    assert "NoBackupAvailableError" in (result.error or "")
    assert "db-old" in h.provider.instances
    assert not any(c.startswith(("delete", "create", "restore")) for c in h.calls)


@pytest.mark.asyncio
async def test_restored_content_mismatch_halts_with_writes_still_frozen(tmp_path: Path):
    h = Harness(tmp_path)

    async def wrong_snapshot() -> dict[str, Any]:
        h.calls.append("snapshot")
        return {**SNAPSHOT, "exercises": []} if "deploy" in h.calls else SNAPSHOT

    h.admin.fetch_snapshot = wrong_snapshot  # type: ignore[method-assign]

    result = await h.cycle()

    assert result is not None and result.phase == "halted"
    assert result.halted_from == "repointed"
    assert "freeze:False" not in h.calls  # az írások zárolva maradnak


# ---- practice restore: real backend endpoints against the restored database ---------------------


@pytest.mark.asyncio
async def test_rehearsal_restore_verifies_content_rule_results_and_ids(tmp_path: Path, monkeypatch):
    """A valódi rehearse_restore: a backend saját olvasó végpontjai a
    visszaállított adatbázison ugyanazt adják-e, mint a mentéskor, és egy
    új rekord ID-ja nem ütközik. Csak a pg_restore-t helyettesíti egy
    SQLite-fájlmásolás (a tesztkörnyezetben nincs Postgres)."""
    import shutil
    from datetime import date

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from backend.db import Base
    from backend.models import Exercise, SetEntry, WorkoutSession
    from maintenance import rehearsal

    source = tmp_path / "source.db"
    engine = create_engine(f"sqlite:///{source}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Exercise(id=1, name="Guggolás", category="legs"))
        for day, weight in [(1, 100.0), (8, 105.0), (15, 110.0)]:
            session = WorkoutSession(session_date=date(2026, 9, day))
            session.sets.append(SetEntry(exercise_id=1, weight_kg=weight, reps=5, set_number=1))
            db.add(session)
        db.commit()
    engine.dispose()

    def copy_restore(meta: BackupMetadata, target_dsn: str) -> None:
        shutil.copyfile(meta.dump_path, target_dsn.removeprefix("sqlite:///"))

    monkeypatch.setattr(rehearsal, "restore_database", copy_restore)

    # A mentéskori pillanatkép: a backend végpontjai a forrás egy másolatán.
    source_copy = tmp_path / "source_view.db"
    shutil.copyfile(source, source_copy)
    expected = await _snapshot_of(f"sqlite:///{source_copy}")
    assert expected["progression"]["1"]["status"] == "improving"

    meta = BackupMetadata("src", NOW.isoformat(), str(source), "x", expected)
    await rehearsal.rehearse_restore(meta, f"sqlite:///{tmp_path / 'rehearsal.db'}")

    tampered = json.loads(json.dumps(expected))
    tampered["progression"]["1"]["status"] = "plateau"
    with pytest.raises(rehearsal.RehearsalFailedError, match="progression: exercise 1"):
        await rehearsal.rehearse_restore(
            BackupMetadata("src", NOW.isoformat(), str(source), "x", tampered), f"sqlite:///{tmp_path / 'r2.db'}"
        )


async def _snapshot_of(dsn: str) -> dict[str, Any]:
    """A backend olvasó végpontjainak válasza egy adott adatbázison."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.db import get_db
    from backend.main import app
    from maintenance.snapshot import fetch_snapshot

    engine = create_engine(dsn, connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine)

    def db_override():  # type: ignore[no-untyped-def]
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = db_override
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            return await fetch_snapshot(client)
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


# ---- Render client: bounded retries -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "statuses", "expected_error", "expected_calls"),
    [
        ("get", [503, 503, 200], None, 3),  # átmeneti hiba: újrapróbál
        ("get", [429, 200], None, 2),  # rate limit: újrapróbál
        ("get", [503, 503, 503, 503], TransientRenderError, 4),  # korlátozott számban
        ("get", [401, 200], PermissionOrQuotaError, 1),  # jogosultság: soha
        ("create", [500, 201], AmbiguousRenderError, 1),  # nem idempotens: nem vakon
    ],
)
async def test_render_client_retries_only_transient_errors(method, statuses, expected_error, expected_calls):
    responses = iter(statuses)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(next(responses), json={"id": "db-x", "status": "available"})

    async def no_sleep(_seconds: float) -> None:
        return None

    async with RenderClient("k", transport=httpx.MockTransport(handler), sleep=no_sleep) as client:
        if method == "get":
            call = client.get_postgres("db-x")
        else:
            call = client.create_postgres(name="n", owner_id="o", plan="free", region="frankfurt", version="16")
        if expected_error is None:
            assert (await call)["id"] == "db-x"
        else:
            with pytest.raises(expected_error):
                await call
    assert len(seen) == expected_calls
