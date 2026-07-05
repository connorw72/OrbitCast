"""CelesTrak GP fetch-with-cache (CLAUDE.md §4.1, F5).

The rate limit is strict and socially enforced: fetch at most once per 2 hours,
serve everything else from the on-disk cache. Stale data degrades gracefully
(positions drift slowly). Tests inject a fake clock and a fake fetch so CI never
touches the live endpoint.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from orbitcast_core.celestrak import fetch_with_cache

_RECORDS = '[{"OBJECT_NAME": "STARLINK-1", "NORAD_CAT_ID": 44714}]'
_NEWER = '[{"OBJECT_NAME": "STARLINK-2", "NORAD_CAT_ID": 44715}]'
_T0 = datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)


class _Fetcher:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return self.payload


def test_first_call_fetches_and_writes_cache(tmp_path) -> None:
    fetch = _Fetcher(_RECORDS)
    records = fetch_with_cache(tmp_path, now=_T0, fetch=fetch)
    assert fetch.calls == 1
    assert records == json.loads(_RECORDS)
    assert list(tmp_path.glob("starlink_gp_*.json"))  # cached to disk


def test_within_two_hours_serves_cache_without_refetching(tmp_path) -> None:
    fetch = _Fetcher(_RECORDS)
    fetch_with_cache(tmp_path, now=_T0, fetch=fetch)
    # 1h59m later a second fetcher is provided but must NOT be called.
    later = _Fetcher(_NEWER)
    records = fetch_with_cache(tmp_path, now=_T0 + timedelta(hours=1, minutes=59), fetch=later)
    assert later.calls == 0
    assert records == json.loads(_RECORDS)


def test_after_two_hours_refetches(tmp_path) -> None:
    fetch_with_cache(tmp_path, now=_T0, fetch=_Fetcher(_RECORDS))
    later = _Fetcher(_NEWER)
    records = fetch_with_cache(tmp_path, now=_T0 + timedelta(hours=2, seconds=1), fetch=later)
    assert later.calls == 1
    assert records == json.loads(_NEWER)
    assert len(list(tmp_path.glob("starlink_gp_*.json"))) == 2


def test_fetch_failure_falls_back_to_stale_cache(tmp_path) -> None:
    fetch_with_cache(tmp_path, now=_T0, fetch=_Fetcher(_RECORDS))

    def boom() -> str:
        raise RuntimeError("celestrak down")

    # Well past the interval, but the network fails: serve stale rather than error.
    records = fetch_with_cache(tmp_path, now=_T0 + timedelta(hours=12), fetch=boom)
    assert records == json.loads(_RECORDS)


def test_fetch_failure_with_no_cache_raises(tmp_path) -> None:
    def boom() -> str:
        raise RuntimeError("celestrak down")

    with pytest.raises(RuntimeError):
        fetch_with_cache(tmp_path, now=_T0, fetch=boom)
