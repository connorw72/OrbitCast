"""Takeaways engine: verdict-first translation of the forecast (design spec Part 2).

The quantile horizon is under-translated, not under-trained: this module turns the
48 h q10/q50/q90 bands into a verdict, one plain-language headline, a confidence
level, and concrete windows (congestion / rain / best). It is pure — no I/O, no
model access — so the server is the single source of phrasing and the frontend
only renders.

Honesty rules (F-registry): every detail string is relative to the cell's *own*
rolling median, never an absolute promise; when no median exists (latitude-prior
basis — currently the live path, since the cell_label_stats mart is not produced
yet), congestion and rain detection are skipped and the payload says so via low
confidence rather than inventing a baseline.
"""

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from .fallback import Basis

# --- thresholds (all relative to the cell's rolling median) ----------------------

# A congestion hour: download q50 below 80% of normal, or latency q50 above 125%.
# Wide enough that ordinary quantile wobble doesn't flag, tight enough to catch
# the evening curve the model actually learns (§6.1).
CONGESTION_DL_RATIO = 0.80
CONGESTION_LAT_RATIO = 1.25

# "Notable" severity: the dip a user would feel in a speed test or a video call.
NOTABLE_DL_RATIO = 0.65
NOTABLE_LAT_RATIO = 1.50

# One bad hour is quantile noise; two consecutive hours is a pattern (spec Part 2).
MIN_CONGESTION_RUN_H = 2

# Verdict "rough" needs a notable window at least this long — a sustained problem,
# not a blip.
ROUGH_NOTABLE_RUN_H = 3

# Open-Meteo drizzle floor: below 0.5 mm/h rain fade is not plausible (WetLinks).
PRECIP_MEANINGFUL_MM_H = 0.5

# The "best window" recommendation length: long enough to schedule something.
BEST_WINDOW_H = 3

# Mean relative band width ((q90-q10)/q50) above which the model is telling us it
# doesn't know; confidence drops one step (spec Part 2 confidence rule).
WIDE_BAND_RELATIVE = 0.8

_CONFIDENCE_BY_BASIS: dict[str, str] = {
    "cell": "high",
    "region": "medium",
    "latitude_prior": "low",
}
_DAYPARTS = ((5, 12, "morning"), (12, 17, "afternoon"), (17, 22, "evening"))


def compute_takeaways(
    horizon: Sequence[Mapping],
    latency_median: float,
    dl_median: float,
    basis: Basis,
    lon: float,
) -> dict:
    """Verdict + headline + confidence + windows for an assemble_payload horizon.

    ``latency_median`` / ``dl_median`` are the cell's rolling medians per target
    (NaN when the label marts provide none — that trigger is then skipped).
    ``lon`` phrases dayparts in local solar time, matching the model's own
    local-solar-time feature.
    """
    hours = [datetime.fromisoformat(str(e["hour"])) for e in horizon]
    has_lat_median = not math.isnan(latency_median)
    has_dl_median = not math.isnan(dl_median)

    congestion = (
        _congestion_windows(horizon, hours, latency_median, dl_median, lon)
        if (has_lat_median or has_dl_median)
        else []
    )
    rain = _rain_windows(
        horizon, hours, congestion_hours=_flagged_hours(congestion, hours), lon=lon
    )
    windows = congestion + rain
    best = _best_window(horizon, hours, lon)
    if best is not None:
        windows = windows + [best]

    verdict = _verdict(congestion + rain)
    confidence = _confidence(horizon, basis, has_median=has_lat_median or has_dl_median)
    return {
        "verdict": verdict,
        "headline": _headline(verdict, congestion + rain, hours, lon),
        "confidence": confidence,
        "windows": windows,
    }


# --- window detection -------------------------------------------------------------


def _q50(entry: Mapping, target: str) -> float | None:
    band = entry.get(target)
    return None if band is None else float(band["q50"])


def _hour_flags(
    entry: Mapping, latency_median: float, dl_median: float
) -> tuple[bool, bool, float, str]:
    """(mild, notable, worst_deviation_ratio, driving_target) for one hour.

    Deviation ratio is "fraction worse than normal" on whichever target deviates
    hardest, so the detail string reports the number a user would notice.
    """
    mild = notable = False
    worst = 0.0
    driver = ""
    dl = _q50(entry, "dl")
    if dl is not None and not math.isnan(dl_median) and dl_median > 0:
        ratio = dl / dl_median
        if ratio < CONGESTION_DL_RATIO:
            mild = True
            notable = notable or ratio < NOTABLE_DL_RATIO
            if 1 - ratio > worst:
                worst, driver = 1 - ratio, "dl"
    lat = _q50(entry, "latency")
    if lat is not None and not math.isnan(latency_median) and latency_median > 0:
        ratio = lat / latency_median
        if ratio > CONGESTION_LAT_RATIO:
            mild = True
            notable = notable or ratio > NOTABLE_LAT_RATIO
            if ratio - 1 > worst:
                worst, driver = ratio - 1, "latency"
    return mild, notable, worst, driver


def _congestion_windows(
    horizon: Sequence[Mapping],
    hours: Sequence[datetime],
    latency_median: float,
    dl_median: float,
    lon: float,
) -> list[dict]:
    flags = [_hour_flags(e, latency_median, dl_median) for e in horizon]
    windows: list[dict] = []
    i = 0
    while i < len(flags):
        if not flags[i][0]:
            i += 1
            continue
        j = i
        while j < len(flags) and flags[j][0]:
            j += 1
        if j - i >= MIN_CONGESTION_RUN_H:
            run = flags[i:j]
            worst, driver = max(((f[2], f[3]) for f in run), key=lambda t: t[0])
            pct = round(worst * 100)
            detail = (
                f"downloads ~{pct}% below your area's normal"
                if driver == "dl"
                else f"latency ~{pct}% above your area's normal"
            )
            windows.append(
                {
                    "kind": "congestion",
                    "start": hours[i].isoformat(),
                    "end": _end_iso(hours, j),
                    "severity": "notable" if any(f[1] for f in run) else "mild",
                    "detail": detail,
                }
            )
        i = j
    return windows


def _rain_windows(
    horizon: Sequence[Mapping],
    hours: Sequence[datetime],
    congestion_hours: set[datetime],
    lon: float,
) -> list[dict]:
    """Runs of meaningful precipitation that overlap degraded hours.

    Rain alone is not a takeaway (the model may predict right through it); rain
    *coinciding with a predicted dip* earns a window naming weather as the likely
    cause. With no congestion baseline there are no degraded hours, so no rain
    windows — same honesty rule.
    """
    wet = [
        float(e.get("weather", {}).get("precip_mm_h", 0.0)) > PRECIP_MEANINGFUL_MM_H
        for e in horizon
    ]
    windows: list[dict] = []
    i = 0
    while i < len(wet):
        if not wet[i]:
            i += 1
            continue
        j = i
        while j < len(wet) and wet[j]:
            j += 1
        if any(h in congestion_hours for h in hours[i:j]):
            windows.append(
                {
                    "kind": "rain",
                    "start": hours[i].isoformat(),
                    "end": _end_iso(hours, j),
                    "severity": "mild",
                    "detail": "rain overlaps this dip — weather is the likely cause",
                }
            )
        i = j
    return windows


def _best_window(horizon: Sequence[Mapping], hours: Sequence[datetime], lon: float) -> dict | None:
    """The calmest BEST_WINDOW_H-hour stretch: lowest summed latency q50."""
    lats = [_q50(e, "latency") for e in horizon]
    if len(lats) < BEST_WINDOW_H or any(v is None for v in lats):
        return None
    sums = [
        sum(lats[k] for k in range(i, i + BEST_WINDOW_H))  # type: ignore[misc]
        for i in range(len(lats) - BEST_WINDOW_H + 1)
    ]
    i = min(range(len(sums)), key=sums.__getitem__)
    start = hours[i]
    return {
        "kind": "best",
        "start": start.isoformat(),
        "end": _end_iso(hours, i + BEST_WINDOW_H),
        "detail": f"calmest {BEST_WINDOW_H}-hour stretch — {_phrase(start, lon)}",
    }


def _flagged_hours(windows: Sequence[Mapping], hours: Sequence[datetime]) -> set[datetime]:
    flagged: set[datetime] = set()
    for w in windows:
        start = datetime.fromisoformat(str(w["start"]))
        end = datetime.fromisoformat(str(w["end"]))
        flagged.update(h for h in hours if start <= h < end)
    return flagged


def _end_iso(hours: Sequence[datetime], j: int) -> str:
    """End-exclusive window bound; the horizon edge closes at last hour + 1 h."""
    if j < len(hours):
        return hours[j].isoformat()
    return (hours[-1] + timedelta(hours=1)).isoformat()


# --- verdict / confidence / phrasing ----------------------------------------------


def _verdict(problem_windows: Sequence[Mapping]) -> str:
    if not problem_windows:
        return "smooth"
    for w in problem_windows:
        start = datetime.fromisoformat(str(w["start"]))
        end = datetime.fromisoformat(str(w["end"]))
        if w.get("severity") == "notable" and end - start >= timedelta(hours=ROUGH_NOTABLE_RUN_H):
            return "rough"
    return "mixed"


def _confidence(horizon: Sequence[Mapping], basis: Basis, has_median: bool) -> str:
    if not has_median:
        return "low"  # no baseline to judge against — say so, don't invent one
    level = _CONFIDENCE_BY_BASIS.get(basis, "low")
    widths = []
    for e in horizon:
        band = e.get("latency")
        if band is not None and float(band["q50"]) > 0:
            widths.append((float(band["q90"]) - float(band["q10"])) / float(band["q50"]))
    if widths and (sum(widths) / len(widths)) > WIDE_BAND_RELATIVE:
        level = {"high": "medium", "medium": "low", "low": "low"}[level]
    return level


def _phrase(when: datetime, lon: float) -> str:
    """'Monday evening'-style phrasing in local *solar* time (lon/15 h offset) —
    the same locality notion as the model's own time features."""
    local = when + timedelta(hours=lon / 15.0)
    daypart = "night"
    for lo, hi, name in _DAYPARTS:
        if lo <= local.hour < hi:
            daypart = name
            break
    return f"{local.strftime('%A')} {daypart}"


def _headline(
    verdict: str, problem_windows: Sequence[Mapping], hours: Sequence[datetime], lon: float
) -> str:
    if verdict == "smooth" or not problem_windows:
        return "Smooth sailing — no rough patches expected in the next 48 hours"

    def _sev_key(w: Mapping) -> tuple[int, datetime]:
        return (
            1 if w.get("severity") == "notable" else 0,
            # earlier windows win ties: they matter to the user sooner
            datetime.fromisoformat(str(w["start"])),
        )

    worst = max(problem_windows, key=lambda w: (_sev_key(w)[0], -_sev_key(w)[1].timestamp()))
    phrase = _phrase(datetime.fromisoformat(str(worst["start"])), lon)
    if verdict == "rough":
        return f"Rough patch ahead — expect a slowdown {phrase}"
    extra = len(problem_windows) - 1
    suffix = f" (+{extra} more)" if extra > 0 else ""
    return f"Mostly smooth — one rough stretch {phrase}{suffix}"
