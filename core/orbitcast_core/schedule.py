"""Deterministic Starlink link-reconfiguration schedule (CLAUDE.md §4.1).

Starlink reallocates user-terminal<->satellite links on a fixed global schedule:
at 12, 27, 42, and 57 seconds past every UTC minute. Latency spikes cluster at
these instants. This is pure arithmetic and needs no orbital data — it is a global
clock, never stored as gridded data (CLAUDE.md §5.3).
"""

from datetime import UTC, datetime, timedelta

RECONFIG_SECONDS: tuple[int, int, int, int] = (12, 27, 42, 57)


def _to_utc(now: datetime) -> datetime:
    """Normalize to UTC. Naive datetimes are assumed to already be UTC."""
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def seconds_to_reconfig(now: datetime) -> float:
    """Seconds from `now` until the next reconfiguration instant.

    On a boundary exactly, returns the full interval to the *following* instant
    (a live countdown resets rather than sitting at zero).
    """
    return (next_reconfig(now) - _to_utc(now)).total_seconds()


def next_reconfig(now: datetime) -> datetime:
    """The next UTC reconfiguration instant strictly after `now`."""
    now = _to_utc(now)
    second_in_minute = now.second + now.microsecond / 1_000_000
    minute_start = now.replace(second=0, microsecond=0)
    for boundary in RECONFIG_SECONDS:
        if boundary > second_in_minute:
            return minute_start + timedelta(seconds=boundary)
    # Past :57 -> wrap to :12 of the next minute.
    return minute_start + timedelta(minutes=1, seconds=RECONFIG_SECONDS[0])
