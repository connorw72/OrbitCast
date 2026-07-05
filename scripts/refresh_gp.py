"""Operator script: refresh the CelesTrak GP cache (Phase 1).

Respects the 2-hour rate limit via fetch_with_cache. In Phase 2 this becomes the
Dagster `celestrak_refresh` job; until then run it manually to populate data/.

    uv run python scripts/refresh_gp.py
"""

from orbitcast_api.config import get_settings
from orbitcast_core.celestrak import fetch_with_cache


def main() -> None:
    cache_dir = get_settings().celestrak_dir
    records = fetch_with_cache(cache_dir)
    print(f"CelesTrak cache OK: {len(records)} objects in {cache_dir}")


if __name__ == "__main__":
    main()
