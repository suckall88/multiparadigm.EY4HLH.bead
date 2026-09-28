"""Bounded retry helper for the maintenance program's network operations.

Section 6.4: újrapróbálkozás csak korlátozott számban, és csak átmeneti
(hálózati, 5xx) vagy rate-limit (429) hibákra. Jogosultsági/kvóta hibát
sosem próbálunk újra — azt a hívó kapja meg azonnal.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

Sleep = Callable[[float], Awaitable[None]]


async def with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    retry_on: tuple[type[BaseException], ...],
    attempts: int = 4,
    base_delay_seconds: float = 2.0,
    description: str = "operation",
    sleep: Sleep = asyncio.sleep,
) -> T:
    """Run `operation`, retrying only `retry_on` errors, at most `attempts` times.

    Exponenciális várakozás a próbálkozások között (2, 4, 8 mp...). Az
    utolsó sikertelen próbálkozás kivételét változatlanul továbbdobja.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except retry_on as exc:
            if attempt == attempts:
                logger.error("%s failed after %d attempts: %s", description, attempts, exc)
                raise
            delay = base_delay_seconds * 2 ** (attempt - 1)
            logger.warning("%s failed (attempt %d/%d): %s; retrying in %.0fs", description, attempt, attempts, exc, delay)
            await sleep(delay)
    raise AssertionError("unreachable")
