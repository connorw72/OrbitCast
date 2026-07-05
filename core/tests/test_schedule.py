"""Deterministic Starlink link-reconfiguration schedule.

Links reallocate at 12, 27, 42, 57 seconds past every UTC minute. These are pure
arithmetic — no orbital data. The wraparound (57 -> next minute's 12) is the case
the plan explicitly calls out.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from orbitcast_core.schedule import (
    RECONFIG_SECONDS,
    next_reconfig,
    seconds_to_reconfig,
)


def _t(h: int, m: int, s: float) -> datetime:
    whole = int(s)
    micro = round((s - whole) * 1_000_000)
    return datetime(2026, 7, 4, h, m, whole, micro, tzinfo=UTC)


def test_schedule_constant_is_the_published_grid() -> None:
    assert RECONFIG_SECONDS == (12, 27, 42, 57)


def test_from_minute_start_next_is_twelve_seconds() -> None:
    assert seconds_to_reconfig(_t(12, 0, 0.0)) == pytest.approx(12.0)


def test_wraparound_after_57_goes_to_next_minute_12() -> None:
    # 57 -> 12 of the following minute = 15 seconds away.
    assert seconds_to_reconfig(_t(12, 0, 57.0)) == pytest.approx(15.0)


def test_just_before_the_wrap() -> None:
    assert seconds_to_reconfig(_t(12, 0, 59.5)) == pytest.approx(12.5)


def test_on_a_boundary_returns_full_interval_to_the_next() -> None:
    # Exactly at :12 we just reconfigured; the next instant is :27, 15s away.
    assert seconds_to_reconfig(_t(12, 0, 12.0)) == pytest.approx(15.0)


def test_mid_interval() -> None:
    assert seconds_to_reconfig(_t(12, 0, 20.0)) == pytest.approx(7.0)


def test_next_reconfig_instant_within_minute() -> None:
    assert next_reconfig(_t(12, 0, 5.0)) == _t(12, 0, 12.0)


def test_next_reconfig_instant_wraps_the_minute() -> None:
    assert next_reconfig(_t(12, 0, 58.0)) == _t(12, 1, 12.0)


def test_non_utc_input_is_normalized_to_utc() -> None:
    # A tz-aware non-UTC time must be converted to UTC first, since the schedule
    # is defined on UTC minute boundaries.
    est = timezone(timedelta(hours=-5))
    t = datetime(2026, 7, 4, 7, 0, 5, tzinfo=est)  # == 12:00:05 UTC
    assert seconds_to_reconfig(t) == pytest.approx(7.0)
