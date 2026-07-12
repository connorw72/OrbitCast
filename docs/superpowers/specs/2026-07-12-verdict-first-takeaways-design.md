# Verdict-first takeaways + fast forecast serving — design

**Date:** 2026-07-12
**Status:** Approved (brainstorming session)

## Problem

Two complaints, one theme: the site shows data, slowly, instead of takeaways, fast.

1. **Comprehension + visuals.** The dashboard buries its conclusion. A visitor gets a
   countdown, five stat tiles, a gauge, and only then a single timid summary sentence
   above a quantile chart. The copy that explains the forecast (`basis` footnote, band
   semantics) reads as jargon to an r/Starlink visitor.
2. **Serving speed.** `GET /v1/forecast` takes ~195–280 s per request and recomputes
   every time; `GET /v1/map` exceeds 300 s. Root cause identified:
   `orbitcast_core.orbital.sky_view` propagates ~8,000 satellites in a Python
   for-loop, once per timestep — 48 timesteps per forecast ≈ 384k skyfield calls.
   CLAUDE.md §4.1 requires vectorizing over the satellite axis; the current code
   does not. Secondary costs: `resolve_ookla` / `resolve_median` re-read whole
   Parquet marts to Python lists on every request, and the `forecast_cache` table
   (CLAUDE.md §7.2) is never used by the serving path.

"Make the ML model better" was clarified to mean **more useful outputs** and
**faster serving** — not accuracy work. The promoted model already beats
persistence with calibrated coverage (~81%); its output is under-translated, not
under-trained.

## Non-goals

- No model retraining, feature additions, or hyperparameter tuning.
- No new data sources.
- No sweeping visual redesign — everything stays inside the existing flight-ops
  design system (tokens in `frontend/src/styles.css :root`).
- No change to section order of the dashboard beyond the forecast section itself.

## Part 1 — Serving performance

### 1a. Vectorized propagation (root-cause fix)

Add `sky_view_series(satellites, lat, lon, times) -> list[SkyView]` to
`orbitcast_core.orbital`:

- Propagate **all satellites × all timesteps in one call** via sgp4's
  `SatrecArray` (skyfield already depends on sgp4), yielding TEME positions.
- Convert TEME → ECEF (GMST rotation) → local ENU → elevation + slant range with
  pure numpy array math. No per-satellite Python loop.
- The existing skyfield per-satellite path stays as the **test oracle**: the
  vectorized results must match it within tolerance (elevation ±0.2°, range
  ±5 km) on a sampled subset of satellites/times. Failing test first.
- Both callers switch to it: `api/orbitcast_api/forecast.py::get_orbital_series`
  (48 timesteps) and the skyview route (1 timestep).

Expected effect: cold forecast ~280 s → low single-digit seconds.

### 1b. `forecast_cache` write-through

Use the existing Postgres `forecast_cache` table
(`pk(h3_cell, hour_utc, metric)` with q10/q50/q90, basis, model_version):

- On a forecast request: read cached rows for `(cell, hour ∈ horizon, metric)`
  **where `model_version` = the currently promoted version**; compute only the
  missing hours; upsert the newly computed rows.
- Rows for superseded model versions are simply never matched (no eviction job
  needed in v1; table stays small at active-cell scale).
- Weather is the one input that changes within an hour bucket: cached rows are
  keyed by hour, and weather itself is already cached per (cell, hour), so a
  cached forecast row is exactly as fresh as the weather cache — acceptable.
- `/v1/map` reads through the same cache per cell and additionally keeps its
  spec'd 1-hour in-process cache of the final aggregate payload (§7.3).

### 1c. Mart memoization

`resolve_ookla` and `resolve_median` currently call `pq.read_table(...).to_pylist()`
per request. Memoize parsed mart contents by `(path, mtime)` exactly like
`satellites.load_satellites` does, and index lookups by `h3_cell` dict instead of
linear scan.

## Part 2 — Takeaways engine

New pure module `ml/orbitcast_ml/takeaways.py`. Input: the 48 h horizon
(q10/q50/q90 for latency + dl, per-hour weather), the cell's rolling median, and
`basis`. Output (added to `ForecastResponse` as `takeaways`):

```
takeaways: {
  verdict: "smooth" | "mixed" | "rough",
  headline: str,                      # "Mostly smooth — one rough evening Friday"
  confidence: "high" | "medium" | "low",
  windows: [
    { kind: "congestion" | "rain" | "best",
      start: iso8601, end: iso8601,
      severity: "mild" | "notable",   # congestion/rain only
      detail: str }                   # "downloads ~35% below your area's normal"
  ]
}
```

Rules (all thresholds named constants with rationale comments):

- **congestion window:** ≥2 consecutive hours where dl q50 < 80% of the cell's
  rolling median, or latency q50 > 125% of it (whichever median the label marts
  provide for that cell; if only one target has a median, only that trigger
  applies). Adjacent windows merge. Severity `notable` at dl < 65% / latency > 150%.
- **rain window:** hours with meaningful precipitation (> 0.5 mm/h) overlapping
  degraded predictions — names weather as the likely cause.
- **best window:** the calmest 3-hour stretch (lowest summed latency q50). This
  logic **moves out of `App.tsx::ForecastSummary` into the engine** so the server
  is the single source of phrasing; the frontend only renders.
- **verdict:** `rough` if any `notable` window ≥3 h; `smooth` if no windows;
  else `mixed`. Headline generated from verdict + the most severe window.
- **confidence:** from `basis` (cell→high ceiling, region→medium, latitude_prior→low)
  degraded one step when mean relative band width ((q90−q10)/q50) is large.
- **Honesty rules (F-registry):** detail strings are always relative to the
  area's own normal, never absolute promises; when the rolling median is NaN
  (latitude-prior basis), congestion detection is skipped and the payload says so
  via low confidence rather than inventing a baseline.

Unit tests over synthetic horizons: flat (smooth), evening-dip (congestion),
rainy overlap, sparse/NaN-median, wraparound windows at the horizon edge.

## Part 3 — Dashboard restructure

Within the existing flight-ops tokens (consume tokens, never raw hex):

- **Verdict card** leads the forecast section: headline in display type, a
  smooth/mixed/rough status treatment, and a plain confidence line
  ("high confidence — based on measurements from your area") replacing the
  current `basis` footnote as the primary label.
- **Window chips** under the headline, one per takeaway window
  ("Fri 7–11pm · busy evening · downloads ~35% below normal",
  "Best window: Sun 5–8am"). Hover/tap highlights that span on the chart.
- **`ForecastChart` upgrades:** shade congestion windows (visually distinct from
  rain shading), label day boundaries in the visitor's local time, and a
  one-line caption: "the shaded band is where we expect 8 of 10 hours to land."
- **De-jargon pass:** `loc /` label, basis sentences, the "Crunching your
  forecast…" skeleton note (obsolete once serving is ~1s), and one-line
  microcopy on the stat tiles saying why each number matters.
- `ForecastSummary` component is removed (superseded by the verdict card).

## Testing & verification

- TDD throughout (project convention): pytest for the vectorized propagation
  (oracle comparison), cache read/compute/write-back behavior (fake clock,
  version switch), mart memoization, and the takeaways engine.
- Frontend: `npm run build` type-check must pass.
- **Verification evidence:** before/after wall-clock timing of `GET /v1/forecast`
  (cold and cached) and `GET /v1/map` on the dev Mac, recorded in the PR/commit
  message. Target: cold forecast < 5 s, cached < 500 ms, map < 5 s.

## Build order

1. Vectorized `sky_view_series` + oracle tests + switch both callers (biggest
   single win, unblocks everything).
2. Mart memoization.
3. `forecast_cache` write-through + map cache.
4. Takeaways engine (pure module + payload wiring).
5. Frontend: verdict card, chips, chart upgrades, copy pass.
6. End-to-end timing measurement + eval note.
