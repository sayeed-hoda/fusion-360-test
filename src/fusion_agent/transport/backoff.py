"""Reconnect delay. 1s, double, cap 60s. Same curve as the KiCad and AutoCAD plugins."""

from __future__ import annotations

import random

RECONNECT_DELAY_SECONDS = 1
MAX_RECONNECT_DELAY_SECONDS = 60
QUIET_AFTER_ATTEMPTS = 3
MAX_INBOUND_MESSAGE_BYTES = 32 * 1024 * 1024
COMMAND_TIMEOUT_S = 120.0

_JITTER = random.Random()


def backoff_base_seconds(failed_attempts: int) -> int:
    """Seconds to wait before reconnect attempt ``failed_attempts``+1, before jitter."""
    if failed_attempts <= 0:
        return RECONNECT_DELAY_SECONDS
    seconds = RECONNECT_DELAY_SECONDS
    for _ in range(1, failed_attempts):
        if seconds >= MAX_RECONNECT_DELAY_SECONDS:
            break
        seconds *= 2
    return min(seconds, MAX_RECONNECT_DELAY_SECONDS)


def backoff_seconds(failed_attempts: int, rng: random.Random | None = None) -> int:
    """Base delay plus up to +25% jitter, never below the base."""
    seconds = backoff_base_seconds(failed_attempts)
    source = rng if rng is not None else _JITTER
    jitter = source.randint(0, max(1, seconds // 4))
    ceiling = MAX_RECONNECT_DELAY_SECONDS + (MAX_RECONNECT_DELAY_SECONDS // 4)
    return min(seconds + jitter, ceiling)
