"""Feature engineering for the forecast models (CLAUDE.md §6.2).

A curated ~15 features, no kitchen sink. FEATURE_COLUMNS is the canonical,
positionally-ordered feature vector consumed by every LightGBM booster; the
training-matrix builder and serving-time inference must produce columns in
exactly this order.

This module owns only the transforms derivable from a timestamp and the cell's
longitude (cyclical hour, day-of-week, local-solar offset). The remaining
features (orbital counts, weather, Ookla context, rolling cell median, source
weight) are assembled by the training-matrix builder / serving path from the
warehouse and passed through positionally.
"""

import math
from datetime import UTC, datetime

# Positional feature vector — order is load-bearing. Do not reorder without
# retraining every booster.
FEATURE_COLUMNS: list[str] = [
    "hour_sin",
    "hour_cos",
    "day_of_week",
    "local_solar_offset_h",
    "precip_mm_h",
    "precip_lag_1h",
    "precip_forecast_3h",
    "sats_visible",
    "max_elevation_deg",
    "cell_lat",
    "terrestrial_baseline_mbps",
    "devices",
    "cell_median_7d",
    "source_quality",
]


def time_features(when: datetime, lon: float) -> dict[str, float]:
    """Time/location-derived features for an instant at a cell.

    - ``hour_sin``/``hour_cos``: cyclical encoding of the UTC hour-of-day
      (fractional, so :30 sits between :00 and :01).
    - ``day_of_week``: Monday=0 .. Sunday=6.
    - ``local_solar_offset_h``: longitude/15, the hours by which local solar
      time leads UTC. Combined with the cyclical UTC hour it lets the model
      reconstruct local evening congestion (§6.1.1) without storing wall-clock
      local time.

    Naive datetimes are assumed to already be UTC.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    when = when.astimezone(UTC)

    decimal_hour = when.hour + when.minute / 60 + when.second / 3600
    angle = 2 * math.pi * decimal_hour / 24
    return {
        "hour_sin": math.sin(angle),
        "hour_cos": math.cos(angle),
        "day_of_week": float(when.weekday()),
        "local_solar_offset_h": lon / 15.0,
    }
