"""Takeaways engine (design spec Part 2).

Pure translation of a 48 h quantile horizon into a verdict, headline, confidence,
and windows (congestion / rain / best). Thresholds are relative to the cell's own
rolling median — never absolute promises — and with no median available the engine
must skip congestion detection and say so via low confidence rather than invent a
baseline (F-registry honesty rules).
"""

from datetime import UTC, datetime, timedelta

from orbitcast_ml.takeaways import (
    CONGESTION_DL_RATIO,
    compute_takeaways,
)

_T0 = datetime(2026, 7, 13, 0, tzinfo=UTC)  # a Monday, 00:00 UTC


def _horizon(
    latency_q50=None,
    dl_q50=None,
    precip=None,
    n: int = 48,
    rel_width: float = 0.4,
) -> list[dict]:
    """Synthetic assemble_payload-shaped horizon. Per-hour overrides via lists."""
    out = []
    for i in range(n):
        lat = 30.0 if latency_q50 is None else latency_q50[i]
        dl = 100.0 if dl_q50 is None else dl_q50[i]
        rain = 0.0 if precip is None else precip[i]
        half = rel_width / 2
        out.append(
            {
                "hour": (_T0 + timedelta(hours=i)).isoformat(),
                "basis": "cell",
                "latency": {"q10": lat * (1 - half), "q50": lat, "q90": lat * (1 + half)},
                "dl": {"q10": dl * (1 - half), "q50": dl, "q90": dl * (1 + half)},
                "weather": {"precip_mm_h": rain},
            }
        )
    return out


def _iso(i: int) -> str:
    return (_T0 + timedelta(hours=i)).isoformat()


def _windows(t: dict, kind: str) -> list[dict]:
    return [w for w in t["windows"] if w["kind"] == kind]


def test_flat_horizon_is_smooth_with_a_best_window():
    t = compute_takeaways(_horizon(), latency_median=30.0, dl_median=100.0, basis="cell", lon=0.0)
    assert t["verdict"] == "smooth"
    assert t["confidence"] == "high"
    assert _windows(t, "congestion") == [] and _windows(t, "rain") == []
    best = _windows(t, "best")
    assert len(best) == 1
    assert best[0].get("severity") is None  # severity applies to congestion/rain only


def test_evening_dip_yields_congestion_window_and_mixed_verdict():
    dl = [100.0] * 48
    for i in (19, 20, 21):  # a 3 h mild evening dip: 75% of normal (< 80% trigger)
        dl[i] = 75.0
    t = compute_takeaways(
        _horizon(dl_q50=dl), latency_median=30.0, dl_median=100.0, basis="cell", lon=0.0
    )
    assert t["verdict"] == "mixed"
    [w] = _windows(t, "congestion")
    assert w["start"] == _iso(19)
    assert w["end"] == _iso(22)  # end-exclusive
    assert w["severity"] == "mild"
    assert "below" in w["detail"] and "%" in w["detail"]


def test_single_bad_hour_is_not_a_window():
    dl = [100.0] * 48
    dl[19] = 70.0  # one hour only — below the >= 2 consecutive-hours rule
    t = compute_takeaways(
        _horizon(dl_q50=dl), latency_median=30.0, dl_median=100.0, basis="cell", lon=0.0
    )
    assert _windows(t, "congestion") == []
    assert t["verdict"] == "smooth"


def test_deep_long_dip_is_rough():
    dl = [100.0] * 48
    for i in range(18, 22):  # 4 h at 60% of normal (< 65% notable trigger)
        dl[i] = 60.0
    t = compute_takeaways(
        _horizon(dl_q50=dl), latency_median=30.0, dl_median=100.0, basis="cell", lon=0.0
    )
    assert t["verdict"] == "rough"
    [w] = _windows(t, "congestion")
    assert w["severity"] == "notable"


def test_latency_trigger_works_without_dl_median():
    lat = [30.0] * 48
    for i in (19, 20):
        lat[i] = 40.0  # 133% of normal (> 125% trigger)
    t = compute_takeaways(
        _horizon(latency_q50=lat),
        latency_median=30.0,
        dl_median=float("nan"),
        basis="cell",
        lon=0.0,
    )
    [w] = _windows(t, "congestion")
    assert "latency" in w["detail"] and "above" in w["detail"]


def test_rain_overlapping_degradation_names_weather():
    dl = [100.0] * 48
    precip = [0.0] * 48
    for i in (14, 15, 16):
        dl[i] = 70.0
        precip[i] = 2.0
    t = compute_takeaways(
        _horizon(dl_q50=dl, precip=precip),
        latency_median=30.0,
        dl_median=100.0,
        basis="cell",
        lon=0.0,
    )
    rain = _windows(t, "rain")
    assert len(rain) == 1
    assert rain[0]["start"] == _iso(14) and rain[0]["end"] == _iso(17)
    assert "rain" in rain[0]["detail"].lower() or "weather" in rain[0]["detail"].lower()


def test_rain_without_degradation_is_not_flagged():
    precip = [0.0] * 48
    for i in (14, 15, 16):
        precip[i] = 2.0  # rain, but predictions are normal
    t = compute_takeaways(
        _horizon(precip=precip), latency_median=30.0, dl_median=100.0, basis="cell", lon=0.0
    )
    assert _windows(t, "rain") == []


def test_nan_medians_skip_congestion_and_cap_confidence_low():
    dl = [100.0] * 48
    for i in range(18, 24):
        dl[i] = 40.0  # would be a screaming dip — but there is no baseline
    t = compute_takeaways(
        _horizon(dl_q50=dl),
        latency_median=float("nan"),
        dl_median=float("nan"),
        basis="latitude_prior",
        lon=0.0,
    )
    assert _windows(t, "congestion") == []
    assert t["confidence"] == "low"
    assert t["verdict"] == "smooth"  # no windows — honest, if uninformative
    assert len(_windows(t, "best")) == 1  # best window needs no baseline


def test_dip_at_horizon_edge_closes_at_the_end():
    dl = [100.0] * 48
    for i in (46, 47):  # dip runs into the end of the horizon
        dl[i] = 70.0
    t = compute_takeaways(
        _horizon(dl_q50=dl), latency_median=30.0, dl_median=100.0, basis="cell", lon=0.0
    )
    [w] = _windows(t, "congestion")
    assert w["start"] == _iso(46)
    assert w["end"] == _iso(48)


def test_wide_bands_degrade_confidence_one_step():
    t = compute_takeaways(
        _horizon(rel_width=1.2),  # q10-q90 spread wider than the q50 itself
        latency_median=30.0,
        dl_median=100.0,
        basis="cell",
        lon=0.0,
    )
    assert t["confidence"] == "medium"


def test_basis_maps_to_confidence_ceiling():
    t = compute_takeaways(_horizon(), latency_median=30.0, dl_median=100.0, basis="region", lon=0.0)
    assert t["confidence"] == "medium"


def test_headline_mentions_the_most_severe_window_daypart():
    dl = [100.0] * 48
    for i in (19, 20, 21):  # 19:00-22:00 UTC at lon 0 -> evening, Monday
        dl[i] = 70.0
    t = compute_takeaways(
        _horizon(dl_q50=dl), latency_median=30.0, dl_median=100.0, basis="cell", lon=0.0
    )
    assert "evening" in t["headline"].lower()
    assert "monday" in t["headline"].lower()


def test_congestion_ratio_is_exclusive_at_the_threshold():
    dl = [100.0] * 48
    for i in (19, 20):
        dl[i] = 100.0 * CONGESTION_DL_RATIO  # exactly at 80% — not below it
    t = compute_takeaways(
        _horizon(dl_q50=dl), latency_median=30.0, dl_median=100.0, basis="cell", lon=0.0
    )
    assert _windows(t, "congestion") == []
