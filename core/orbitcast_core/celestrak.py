"""CelesTrak supplemental GP data, fetch-with-cache (CLAUDE.md D9, §4.1, F5).

Supplemental GP data is derived from SpaceX's own ephemerides and is more accurate
than the general catalog. The rate limit is strict and socially enforced: fetch
each file at most once per 2 hours and serve all computation from the on-disk
cache. This module is the only code allowed to hit the network endpoint.

Stale data degrades gracefully (positions drift slowly), so a fetch failure with
an existing cache serves stale rather than erroring.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Supplemental GP (SpaceX ephemeris-derived); fall back to the general catalog.
SUP_GP_URL = (
    "https://celestrak.org/NORAD/elements/supplemental/sup-gp.php?FILE=starlink&FORMAT=json"
)
FALLBACK_GP_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=json"

MIN_INTERVAL = timedelta(hours=2)
_PREFIX = "starlink_gp_"
_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def _cache_path(cache_dir: Path, now: datetime) -> Path:
    stamp = now.astimezone(UTC).strftime(_STAMP_FORMAT)
    return cache_dir / f"{_PREFIX}{stamp}.json"


def latest_cache(cache_dir: Path) -> Path | None:
    """Most recent cached GP file, or None. Filenames sort chronologically."""
    files = sorted(cache_dir.glob(f"{_PREFIX}*.json"))
    return files[-1] if files else None


def _cache_stamp(path: Path) -> datetime:
    stamp = path.stem.removeprefix(_PREFIX)
    return datetime.strptime(stamp, _STAMP_FORMAT).replace(tzinfo=UTC)


def cache_time(path: Path) -> datetime:
    """The UTC time a cache file was written, parsed from its filename."""
    return _cache_stamp(path)


def http_fetch(url: str = SUP_GP_URL) -> str:
    """Fetch raw GP JSON text from CelesTrak. Not exercised in CI."""
    import httpx

    resp = httpx.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def fetch_with_cache(
    cache_dir: Path,
    now: datetime | None = None,
    fetch: Callable[[], str] = http_fetch,
    min_interval: timedelta = MIN_INTERVAL,
) -> list[dict]:
    """Return parsed GP records, fetching only if the cache is older than
    `min_interval`. On fetch failure, serve stale cache if present."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    cache_dir.mkdir(parents=True, exist_ok=True)
    latest = latest_cache(cache_dir)

    if latest is not None and now - _cache_stamp(latest) < min_interval:
        return json.loads(latest.read_text())

    try:
        raw = fetch()
    except Exception:
        if latest is not None:
            return json.loads(latest.read_text())
        raise

    _cache_path(cache_dir, now).write_text(raw)
    return json.loads(raw)
