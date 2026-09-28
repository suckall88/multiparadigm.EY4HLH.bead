"""Unit tests for the maintenance program's decision and swap-control logic.

These never touch a live external service or real cloud resources: the
provider is a small fake/stub object, and the backup is a plain fixture.

Ez a fájl teljesíti a Section 4 elvárását: a maintenance döntéseket
(átmeneti hiba, hiányzó mentés, megszakadt futás folytatása) FIX,
kézzel felépített adatokkal teszteli, sosem éri el a valódi Rendert
vagy egy igazi adatbázist — helyette egy egyszerű "hamis" (`_FakeProvider`)
objektummal helyettesítjük a Render klienst.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from maintenance.backup import NoBackupAvailableError
from maintenance.db_swap_controller import DBSwapController
from maintenance.decision import decide_swap
from maintenance.models import CheckResult, RunState


def _run_state(phase: str = "idle", **overrides) -> RunState:
    """Segédfüggvény: gyorsan összeállít egy RunState-et a teszteknek,
    csak a ténylegesen érdekes mezőket felülírva."""
    now = datetime.now(timezone.utc)
    defaults = dict(run_id="test-run", phase=phase, started_at=now, updated_at=now)
    defaults.update(overrides)
    return RunState(**defaults)


# ---- decide_swap -------------------------------------------------------------


def test_decide_swap_transient_failure_no_swap():
    # Egy sima elérhetetlenség (pl. időtúllépés) ÖNMAGÁBAN sosem
    # indokolja a cserét — ez a Section 6.1 kifejezett elvárása.
    check = CheckResult(checked_at=datetime.now(timezone.utc), reachable=False, error="timeout")
    decision = decide_swap(check, run_state=None, now=datetime.now(timezone.utc))
    assert decision.should_swap is False
    assert "connectivity" in decision.reason


def test_decide_swap_approaching_expiry_triggers_swap():
    # A lejárat 1 nap múlva van, ami a 3 napos alapértelmezett
    # figyelmeztetési ablakon belülre esik -> cserét kell javasolnia.
    now = datetime.now(timezone.utc)
    check = CheckResult(checked_at=now, reachable=True, expires_at=now + timedelta(days=1))
    decision = decide_swap(check, run_state=None, now=now)
    assert decision.should_swap is True


def test_decide_swap_far_from_expiry_no_swap():
    # 20 nap múlva jár csak le -> még nincs ok a cserére.
    now = datetime.now(timezone.utc)
    check = CheckResult(checked_at=now, reachable=True, expires_at=now + timedelta(days=20))
    decision = decide_swap(check, run_state=None, now=now)
    assert decision.should_swap is False


def test_decide_swap_already_fulfilled_expiry_not_retriggered():
    # Ha egy korábbi, sikeresen befejezett futás már UGYANEZT a
    # lejárati időpontot kezelte le, a szabály nem indíthat újra
    # cserét ugyanazért az eseményért.
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(days=1)
    check = CheckResult(checked_at=now, reachable=True, expires_at=expiry)
    prior_run = _run_state(phase="completed", fulfilled_expiry_at=expiry.isoformat())
    decision = decide_swap(check, run_state=prior_run, now=now)
    assert decision.should_swap is False
    assert "already fulfilled" in decision.reason


# ---- missing backup halts the run ---------------------------------------------


class _FakeProvider:
    """A valódi maintenance/provider_client.py::RenderClient helyett
    használt, teszt célú "hamis" osztály: ugyanazokat az async
    metódusneveket adja, de sosem hív valódi hálózatot — csak egy
    memóriabeli dict-ből szolgálja ki a válaszokat, és számolja, hányszor
    hívták (pl. `create_calls`), hogy a tesztek ellenőrizhessék, NEM
    történt tényleges (vagy duplikált) instance-létrehozás.
    """

    def __init__(self):
        self.create_calls = 0
        self.instances: dict[str, dict] = {}

    async def get_postgres_instance(self, instance_id: str) -> dict:
        return self.instances[instance_id]

    async def create_postgres_instance(self, **kwargs) -> dict:
        self.create_calls += 1
        return {"id": "new-instance", "status": "creating"}

    async def wait_until_available(self, instance_id: str, **kwargs) -> dict:
        return self.instances[instance_id]

    async def delete_postgres_instance(self, instance_id: str) -> None:
        pass


@pytest.mark.asyncio
async def test_decide_swap_missing_backup_halts():
    """Ha nincs elérhető mentés, a 4. lépés (create_and_restore)
    KÖTELEZŐEN NoBackupAvailableError-t dob, és semmiképp sem hoz
    létre "pótlásból" adatot vagy instance-t — ez a Section 6.4
    "sosem fabrikálunk adatot" szabályának közvetlen tesztje."""
    provider = _FakeProvider()
    controller = DBSwapController(
        provider=provider,
        state_store=None,  # not used by create_and_restore directly
        http_client=None,
        source_dsn="postgresql://source",
        source_id="src-1",
    )
    run_state = _run_state(phase="provisioning", target_instance_id="new-instance")

    with pytest.raises(NoBackupAvailableError):
        await controller.create_and_restore(run_state, backup=None)

    # No fabricated instance/data should have been created as a substitute.
    assert provider.create_calls == 0


# ---- resuming an interrupted run reconciles instead of duplicating -----------


@pytest.mark.asyncio
async def test_resume_interrupted_run_does_not_create_duplicate_instance():
    """Ha egy megszakadt futás target-instance-a a Render szerint már
    létezik és elérhető, a `reconcile` nem hoz létre új instance-t
    (create_calls marad 0), hanem egyszerűen folytatja onnan, ahol a
    futás tartott."""
    provider = _FakeProvider()
    provider.instances["already-provisioned"] = {"id": "already-provisioned", "status": "available"}

    controller = DBSwapController(
        provider=provider,
        state_store=None,
        http_client=None,
        source_dsn="postgresql://source",
        source_id="src-1",
    )
    interrupted_run = _run_state(phase="provisioning", target_instance_id="already-provisioned")

    resumed = await controller.reconcile(interrupted_run)

    assert provider.create_calls == 0
    assert resumed.phase == "provisioning"
    assert resumed.target_instance_id == "already-provisioned"


@pytest.mark.asyncio
async def test_resume_interrupted_run_halts_when_instance_unverifiable():
    """Ha a célinstance állapota egyáltalán nem kérdezhető le (itt: a
    `_FakeProvider.instances` dict szándékosan üres, ezért a lekérdezés
    KeyError-t dob), a rendszernek biztonságosan le KELL állnia
    ("halted"), sosem szabad találgatva továbbmennie vagy új
    instance-t létrehoznia."""
    provider = _FakeProvider()  # instance dict deliberately left empty -> lookup fails

    controller = DBSwapController(
        provider=provider,
        state_store=_NullStateStore(),
        http_client=None,
        source_dsn="postgresql://source",
        source_id="src-1",
    )
    interrupted_run = _run_state(phase="provisioning", target_instance_id="missing-instance")

    resumed = await controller.reconcile(interrupted_run)

    assert resumed.phase == "halted"
    assert provider.create_calls == 0


class _NullStateStore:
    """Minimál "hamis" StateStore, ami csak elnyeli a save() hívást —
    a reconcile teszteknek nem kell valódi fájlba írniuk."""

    def save(self, run_state: RunState) -> None:
        pass
