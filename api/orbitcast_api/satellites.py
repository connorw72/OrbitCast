"""Load Starlink satellites from the CelesTrak cache for in-process propagation.

The serving path only *reads* the cache (populated by scripts/refresh_gp.py in
Phase 1, and by the Dagster celestrak_refresh job in Phase 2). It never hits the
CelesTrak network endpoint (CLAUDE.md §4.1). Parsed satellites are memoized and
reloaded only when a newer cache file appears.
"""

import json
from datetime import datetime
from pathlib import Path

from orbitcast_core.celestrak import cache_time, latest_cache
from orbitcast_core.orbital import satellites_from_gp
from skyfield.api import EarthSatellite

from .config import get_settings

_memo: dict[str, object] = {"path": None, "mtime": None, "sats": []}


def load_satellites(cache_dir: Path) -> list[EarthSatellite]:
    latest = latest_cache(cache_dir)
    if latest is None:
        return []
    mtime = latest.stat().st_mtime
    if _memo["path"] == latest and _memo["mtime"] == mtime:
        return _memo["sats"]  # type: ignore[return-value]
    sats = satellites_from_gp(json.loads(latest.read_text()))
    _memo.update(path=latest, mtime=mtime, sats=sats)
    return sats


def get_satellites() -> list[EarthSatellite]:
    return load_satellites(get_settings().celestrak_dir)


def gp_fetched_at(cache_dir: Path | None = None) -> datetime | None:
    latest = latest_cache(cache_dir or get_settings().celestrak_dir)
    return cache_time(latest) if latest is not None else None
