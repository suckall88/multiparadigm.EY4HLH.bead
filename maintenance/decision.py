"""Pure swap-decision logic (functional paradigm: no I/O, deterministic).

Kept separate from the async check itself so it can be unit tested with
plain fixed inputs, per the assignment's requirement to test maintenance
decisions without depending on any live service.

Ez a fájl a maintenance modul funkcionális paradigma-helye: a
decide_swap függvény tisztán a bemeneti adatokból dönt, nem éri el sem
a hálózatot, sem a lemezt — ezért lehet fix, kézzel megírt teszt-
esetekkel (transient hiba, hiányzó backup, megszakadt futás) ellenőrizni.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from maintenance.models import CheckResult, RunState, SwapDecision

DEFAULT_EXPIRY_WARNING_DAYS = 3


def decide_swap(
    check: CheckResult,
    run_state: RunState | None,
    now: datetime,
    maintenance_at: datetime | None = None,
    expiry_warning_days: int = DEFAULT_EXPIRY_WARNING_DAYS,
) -> SwapDecision:
    """Decide whether the current check result warrants starting a swap.

    A mere connectivity failure or missing expiry data never triggers a
    swap on its own. An already-fulfilled maintenance request (the run
    that handled the same expiry timestamp already completed) must not
    re-trigger.

    Bemenetek: a legutóbbi állapotellenőrzés eredménye (`check` —
    a maintenance/provider_client.py aszinkron hívásából), az előző
    futás állapota (`run_state` — a maintenance/state.py::StateStore-ból
    betöltve, vagy None, ha még soha nem futott), a jelenlegi idő, és
    opcionálisan egy konfigurált karbantartási időpont
    (`maintenance_at` — teszteléshez/próbafuttatáshoz, a valós lejárat
    kivárása helyett). Kimenet: egy SwapDecision (kell-e cserélni + miért).
    Ezt hívja a maintenance/db_swap_controller.py minden egyes
    ütemezett ellenőrzés után, mielőtt bármilyen tényleges csere-lépést
    elindítana.
    """
    # 1. szabály: puszta elérhetetlenség (hálózati/kapcsolati hiba)
    # SOSEM indokolja önmagában a cserét — ez csak átmeneti gond is
    # lehet, nem feltétlenül azt jelenti, hogy le fog járni az instance.
    if not check.reachable:
        return SwapDecision(
            should_swap=False,
            reason="source unreachable: a connectivity failure alone does not warrant a swap",
        )

    # 2. szabály: ha egy korábbi futás már sikeresen lekezelte pontosan
    # ugyanezt a lejárati időpontot, ne indítsunk újra cserét csak azért,
    # mert az ellenőrzés megint ugyanazt az expires_at-ot látja.
    if run_state is not None and run_state.phase in ("completed",) and check.expires_at is not None:
        if run_state.fulfilled_expiry_at == check.expires_at.isoformat():
            return SwapDecision(
                should_swap=False,
                reason="maintenance for this expiry timestamp was already fulfilled",
            )

    # 3. szabály: ha be van állítva egy konkrét (pl. próbafuttatáshoz
    # konfigurált) karbantartási időpont, az felülírja a lejárat-alapú
    # döntést — ez teszi lehetővé, hogy ne kelljen kivárni a valós
    # 30 napos Render-lejáratot a rehearsalhoz.
    if maintenance_at is not None:
        if now >= maintenance_at:
            return SwapDecision(should_swap=True, reason="configured maintenance time reached")
        return SwapDecision(
            should_swap=False, reason="configured maintenance time not yet reached"
        )

    # 4. szabály: ha nincs lejárati adat, nem tudunk dönteni — ez sem
    # indokolja önmagában a cserét (üres/hiányzó adat != veszély).
    if check.expires_at is None:
        return SwapDecision(should_swap=False, reason="no expiry data available")

    # 5. szabály: a tényleges trigger — ha a lejárat a beállított
    # figyelmeztetési ablakon (expiry_warning_days) belülre esik.
    if check.expires_at - now <= timedelta(days=expiry_warning_days):
        return SwapDecision(should_swap=True, reason="instance approaching expiry")

    return SwapDecision(should_swap=False, reason="not yet approaching expiry")
