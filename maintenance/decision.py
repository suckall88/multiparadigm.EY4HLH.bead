"""Pure swap-decision logic (functional paradigm: no I/O, deterministic).

Kept separate from the async check itself so it can be unit tested with
plain fixed inputs, per the assignment's requirement to test maintenance
decisions without depending on any live service.

Ez a fájl a maintenance modul funkcionális paradigma-helye: a
függvények tisztán a bemeneti adatokból döntenek, nem érik el sem a
hálózatot, sem a lemezt, sem az órát (a "most" is paraméter).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from maintenance.models import CheckResult, RunState, SwapDecision

DEFAULT_EXPIRY_WARNING_DAYS = 3


def parse_maintenance_at(raw: str, tz: ZoneInfo) -> datetime | None:
    """Parse the configured maintenance time into an aware UTC datetime.

    Időzóna nélküli értéket (pl. "2026-10-01T02:00") a konfigurált
    `tz` szerint értelmez, így az összehasonlítás mindig egyetlen,
    konzisztens időzónában (UTC) történik. Üres szövegre None.
    """
    raw = raw.strip()
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(timezone.utc)


def _due_triggers(
    check: CheckResult,
    now: datetime,
    maintenance_at: datetime | None,
    expiry_warning_days: int,
) -> list[tuple[str, str]]:
    """Az éppen esedékes karbantartási kérések (trigger_key, ok) párjai."""
    due: list[tuple[str, str]] = []
    if maintenance_at is not None and now >= maintenance_at:
        due.append((f"maintenance_at:{maintenance_at.isoformat()}", "configured maintenance time reached"))
    if check.expires_at is not None and check.expires_at - now <= timedelta(days=expiry_warning_days):
        due.append((f"expiry:{check.expires_at.isoformat()}", "instance approaching expiry"))
    return due


def decide_swap(
    check: CheckResult,
    last_run: RunState | None,
    now: datetime,
    maintenance_at: datetime | None = None,
    expiry_warning_days: int = DEFAULT_EXPIRY_WARNING_DAYS,
) -> SwapDecision:
    """Decide whether the current check result warrants starting a swap.

    Szabályok, sorrendben:
    1. Puszta elérhetetlenség (hálózati hiba, hiányzó adat) SOSEM
       indokol cserét — ez átmeneti gond is lehet.
    2. Csak tervezett karbantartás (MAINTENANCE_AT elérve) vagy közelgő
       lejárat (expires_at a figyelmeztetési ablakon belül) indít cserét.
    3. Egy már teljesített kérés (a legutóbbi sikeres futás
       `fulfilled_trigger`-e) nem indít újabb cserét — különben egy
       próbafuttatás MAINTENANCE_AT-ja minden ciklusban újra cserélne.
    """
    if not check.reachable:
        return SwapDecision(
            should_swap=False,
            reason="source unreachable: a connectivity failure alone does not warrant a swap",
        )

    due = _due_triggers(check, now, maintenance_at, expiry_warning_days)
    if not due:
        if check.expires_at is None and maintenance_at is None:
            return SwapDecision(should_swap=False, reason="no expiry data and no maintenance time configured")
        return SwapDecision(should_swap=False, reason="no maintenance due yet")

    fulfilled = last_run.fulfilled_trigger if last_run is not None else None
    for trigger_key, reason in due:
        if trigger_key != fulfilled:
            return SwapDecision(should_swap=True, reason=reason, trigger_key=trigger_key)

    return SwapDecision(should_swap=False, reason="maintenance request already fulfilled")
