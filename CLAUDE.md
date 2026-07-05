# CLAUDE.md — OrbitCast

> **Read this entire document before writing any code.** This file is the single source of
> truth for OrbitCast. It was produced from a full architecture-planning session and contains
> every decision, its rationale, and a phased execution checklist. Nothing in this document
> depends on context outside it. Where a decision is marked **[LOCKED]**, do not revisit it —
> the trade-offs were already argued. Where marked **[VERIFY AT BUILD TIME]**, the fact was
> true as of July 2026 and must be re-checked before relying on it.
>
> Execute the phases in order (§10). Each phase ends in a genuinely usable product increment,
> not just technically complete plumbing. Within a phase, follow the checklist top to bottom.

---

## 1. What OrbitCast is

**Problem.** Starlink users experience intermittent latency spikes and throughput drops caused
by (a) scheduled satellite-link reconfigurations, (b) evening congestion in their coverage
cell, and (c) weather (rain fade). They have no predictive visibility into any of this.

**Product.** A web app where a user enters their location and gets:

1. **Sky view (deterministic, no ML):** how many Starlink satellites are visible from their
   location right now, the elevation of the best one, and a live countdown to the next
   link-reconfiguration instant. Starlink reallocates user-terminal↔satellite links on a
   fixed global schedule: at **12, 27, 42, and 57 seconds past every UTC minute** (confirmed
   by multiple measurement studies, including the Aalborg University testbed work and
   follow-ups). Latency spikes cluster at these instants.
2. **Forecast (ML):** expected latency and download throughput for their area over the next
   48 hours, with uncertainty bands, showing the evening congestion curve and any
   weather-driven degradation windows ("rough patches").
3. **Dish Doctor (ML, requires user data):** given measurements from the user's own
   connection, a verdict on whether their dish underperforms the model's expectation for
   their area and time — per-user anomaly detection with an interpretable explanation.

**Audience & launch.** Launched anonymously to r/Starlink and r/HomeNetworking. The product
must be genuinely useful with zero signup (features 1 and 2); feature 3 is the hook that
converts visitors into contributors of crowdsourced measurements.

**Non-goals (v1).** No mobile app. No paid tier. No other LEO constellations (OneWeb etc.).
No per-satellite "which satellite am I on" claims — that assignment is opaque from outside
SpaceX (see failure mode F3, §9).

---

## 2. Fixed constraints (non-negotiable)

- **C1 — Docker everywhere.** Every service (API, database, orchestrator, scheduled jobs)
  runs in Docker via a single `docker-compose.yml`. Local development uses **OrbStack** on
  Apple Silicon, not Docker Desktop. Consequences you must respect:
  - Build **multi-arch images** (`linux/arm64` for the dev Mac and the ARM EC2 host;
    add `linux/amd64` only if a dependency forces it). Prefer arm64-native base images.
  - Never depend on Docker Desktop-specific behavior. Prefer service-name DNS on the
    compose network over `host.docker.internal` (OrbStack supports it, Linux hosts need
    `extra_hosts` mapping — avoid needing it at all).
  - Use named volumes for Postgres and the DuckDB warehouse file.
- **C2 — Development machine is an Apple Silicon MacBook Air** (fanless, 8–16 GB RAM).
  All training must run comfortably on it. The model choice in §6 was made partly because of
  this. **PyTorch + MPS is deliberately NOT used** — see decision D6.

---

## 3. Decision ledger

Every major decision, stated plainly. Rationale in the referenced sections.

| # | Decision | Status |
|---|----------|--------|
| D1 | Spatial index: **H3, resolution 5**, stored as BIGINT, as the canonical geographic key everywhere. Coarser views derived via `h3_cell_to_parent`. Not S2. | **[LOCKED]** §5 |
| D2 | Offline/analytical store: **DuckDB** (single warehouse file + Parquet artifacts). | **[LOCKED]** §5.4 |
| D3 | Serving store: **Postgres 16** in Docker, **without PostGIS**. H3 keys turn geospatial lookups into btree lookups; PostGIS adds ops weight with no query it uniquely serves here. Revisit only if polygon/radius queries appear. | **[LOCKED]** §7.2 |
| D4 | Orchestration: **Dagster OSS** (single container: webserver + daemon). Not Airflow, not bare cron. | **[LOCKED]** §5.5 |
| D5 | Models: **LightGBM quantile regression** (q10/q50/q90) for latency and throughput baselines; a **deterministic schedule overlay** for the 15-second microstructure; a **quantile-residual statistical test** for per-user anomaly detection. No RNN, no transformer, no autoencoder in v1. | **[LOCKED]** §6 |
| D6 | No PyTorch, no MPS in v1. GBMs are CPU-native and train in minutes at this data scale; MPS pays off only for large dense-tensor workloads that do not exist in this project. | **[LOCKED]** §6.5 |
| D7 | Cold-start data: **M-Lab NDT filtered to Starlink AS14593** for throughput+latency labels, **RIPE Atlas Starlink probes** for continuous latency anchors, **WetLinks** for the rain-fade response curve. **Ookla open data is demoted to a context feature** — it has NO per-ISP breakdown and cannot serve as a Starlink baseline. | **[LOCKED]** §4 |
| D8 | Weather: **Open-Meteo** (no API key, free non-commercial tier, forecast + ERA5 historical archive). | **[LOCKED]** §4.4 |
| D9 | Orbital data: **CelesTrak supplemental GP data for Starlink** (SpaceX-supplied ephemeris, more accurate than general catalog TLEs), fetched at most once per 2 hours, propagated with **skyfield** (sgp4 under the hood, vectorized with numpy). | **[LOCKED]** §4.1 |
| D10 | Hosting: **one `t4g.small` ARM EC2 instance running Docker Compose** (Caddy + API + Postgres + Dagster), S3 for backups/artifacts, **Vercel** for the static frontend, GitHub Actions → GHCR → `compose pull` deploys. **No RDS.** | **[LOCKED]** §8 |
| D11 | Backend: **FastAPI** (Python 3.12, `uv` for dependency management). Frontend: **Next.js static export** (or Vite+React — implementer's choice, but it must build to a static bundle; no SSR server). | **[LOCKED]** §7 |
| D12 | Privacy: user locations stored only as H3 res-5 cells (~250 km²). Never store raw coordinates or raw IPs. Anonymous token auth, no email required. | **[LOCKED]** §9 (F8) |

---

## 4. Data sources — exact contracts

### 4.1 Orbital elements (CelesTrak) — every 2 hours

- URL: `https://celestrak.org/NORAD/elements/supplemental/sup-gp.php?FILE=starlink&FORMAT=json`
  (supplemental GP data derived from SpaceX's own ephemerides). Fallback if unavailable:
  `https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=json`.
- **Rate limit is strict and socially enforced:** fetch each file at most once per 2 hours;
  CelesTrak blocks abusive clients. Cache the raw response to
  `data/raw/celestrak/starlink_gp_{utc_iso_timestamp}.json` and serve all computation from
  cache. The Dagster job (§5.5) is the only code allowed to hit this network endpoint.
- Processing: `skyfield` `EarthSatellite` objects; vectorize over the satellite axis with
  numpy. ~8,000 satellites × 240 timesteps (one hour at 15 s resolution) computes in well
  under a second on the MacBook Air — do not prematurely optimize.
- Derived features per (location, timestamp):
  - `sats_visible`: count with elevation ≥ 25° (Starlink terminal elevation mask).
  - `max_elevation_deg`: elevation of the best satellite.
  - `min_range_km`: slant range to the nearest visible satellite.
  - `seconds_to_reconfig`: seconds until the next of {12, 27, 42, 57} s past the UTC minute
    (pure arithmetic; no orbital data needed).

### 4.2 Starlink-specific performance labels — the training targets

**(a) M-Lab NDT7 — primary throughput + latency source.** Public, ongoing, global. Every
NDT7 test row includes download/upload rate, minRTT, loss, and the **client ASN** — filter
`client.Network.ASNumber = 14593` (SpaceX/Starlink). Access: BigQuery
`measurement-lab.ndt.unified_downloads` / `unified_uploads` (free; requires joining the
M-Lab Google Group with any Google account — **[VERIFY AT BUILD TIME]** the exact
subscription step). Pull monthly extracts to Parquet; stay inside BigQuery's free 1 TB/month
query quota by selecting only needed columns and partition-pruning by date.
**Known defect to design around:** Starlink uses CGNAT; IP geolocation for Starlink clients
frequently snaps to the *PoP/gateway city*, not the user's real location. Treat M-Lab
locations as reliable only at **H3 res 3–4 (regional)** granularity; aggregate to res 4 and
let the hierarchical fallback (§6.3) absorb the imprecision. Do not pretend res-5 precision.

**(b) RIPE Atlas — continuous latency anchors.** ~99 probes on AS14593 across ~32 countries
as of 2025 (count drifts; discover dynamically). Probe coordinates are public (fuzzed ≤ ~1 km
— fine for res 5). Access: `https://atlas.ripe.net/api/v2/probes/?asn_v4=14593&status=1` to
enumerate probes, then fetch their public *builtin* ping measurement results. Reading
existing public results costs **no credits**; only scheduling new measurements does — so v1
consumes builtins only. Daily Dagster job appends to Parquet.

**(c) WetLinks — the rain-fade training set (static, offline).** Six months (Oct 2023 –
Mar 2024) of measurements every 3 minutes at two European sites (Osnabrück DE, Enschede NL),
~140k rows: download/upload throughput, RTT, packet loss, plus co-located weather-station
data. Source: TMA 2024 paper "WetLinks" (arXiv:2402.16448); dataset link is in the paper
**[VERIFY AT BUILD TIME]**. Role: fit and validate the *precipitation → performance
degradation* response used as a feature transform, and sanity-check the 15 s periodic
structure. It is NOT a live source and must never leak into serving-time joins as current data.

**(d) Ookla Open Data — context only.** `s3://ookla-open-data/parquet/performance/...`,
quarterly, zoom-16 Web Mercator tiles with a quadkey column. **No ISP breakdown** — it is
all-providers fixed broadband per tile. Its only legitimate uses here: (1) a
`terrestrial_baseline_mbps` context feature (poor terrestrial options correlate with
Starlink cell load), (2) demand proxy via `tests` and `devices` counts. Read directly from
S3 with DuckDB `httpfs`; aggregate quadkeys → H3 res 5 by converting each tile centroid with
`h3.latlng_to_cell`, weighted by test count.

### 4.3 Crowdsourced user measurements (Phase 4 — the moat)

Two collection paths, both opt-in:
1. **Dish reporter (high value):** a single-file Python script (also shipped as a Docker
   image, per C1) users run on their LAN. It polls the Starlink dish's local gRPC status
   endpoint at `192.168.100.1:9200` (`SpaceX.API.Device.Device/Handle`, `get_status`) for
   `pop_ping_latency_ms`, `downlink_throughput_bps`, `uplink_throughput_bps`, obstruction
   stats, and hardware version, and POSTs batches to the API with the user's anonymous
   token. Follow the community-maintained `starlink-grpc-tools` approach
   **[VERIFY AT BUILD TIME]** for current field names. This is data no public source has:
   per-dish, per-second, with hardware metadata.
2. **Browser probe (zero-install):** the web app measures round-trips over a WebSocket to
   the API (~30 samples over 15 s) and, with consent, submits them tagged with the user's
   H3 res-5 cell. Latency only — browsers cannot measure bulk throughput honestly; don't try.

### 4.4 Weather (Open-Meteo)

- Forecast: `https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&hourly=precipitation,rain,showers,snowfall,cloud_cover&forecast_days=2`
- Historical (for training joins): the ERA5 archive endpoint
  (`https://archive-api.open-meteo.com/v1/archive`) — aligns timestamps with
  WetLinks/Atlas/M-Lab history.
- No API key; free for non-commercial use; ~10k requests/day is the practical courtesy
  ceiling **[VERIFY AT BUILD TIME]**. Therefore **cache per (H3 res-5 cell, hour)** in
  Postgres: a cell's forecast is fetched at most hourly regardless of visitor volume.
  Request weather for the **cell centroid**, never per-user coordinates (privacy, D12).

---

## 5. Spatial & temporal data fusion

### 5.1 Why a discrete global grid at all

A "single location lookup" and a "precomputed dense map" are the same structure: a keyed
lookup table. One canonical key collapses the fusion problem — every source reduces to
`(h3_cell, time_bucket, features...)` and every serving query is a key lookup. Without it,
you're doing runtime point-in-polygon joins across three coordinate systems on a laptop.

### 5.2 H3 over S2, resolution 5 — the argument (D1)

- **Ecosystem fit:** first-class Python bindings (`h3` v4), a DuckDB community extension
  (`INSTALL h3 FROM community;`), and native hex layers in frontend mapping libraries
  (deck.gl `H3HexagonLayer`).
- **Uniform neighbor distance:** the one spatial-smoothing operation this project needs —
  borrowing strength from adjacent cells via `grid_disk` — is clean on hexagons. S2's
  strengths (exact containment hierarchy, region covering) solve problems this project
  doesn't have.
- **Resolution 5** (~252 km² per cell, ~9.8 km edge, ~20 km across) is matched to the
  physical grain of the strongest signal: Starlink's own scheduling cells are ~20–25 km
  across, so congestion varies at exactly this scale. Res 6 triples storage and pretends to
  precision the labels don't have (see M-Lab geo caveat); res 4 blurs weather fronts.
  Global res-5 count is ~2M cells, but the **active set** (cells with any label source or
  any user) is a few hundred thousand at most — trivial for DuckDB/Postgres on this hardware.
- Store H3 indexes as **BIGINT** (the 64-bit cell id), never hex strings, in both stores.

### 5.3 The grains, unified

| Source | Native grain | Fusion rule |
|---|---|---|
| Ookla | quadkey z16 tile, quarterly | tile centroid → res-5 cell, mean weighted by `tests`; one static context table per quarter |
| CelesTrak orbital | continuous, refreshed 2-hourly | *not* pre-gridded globally — computed on demand per requested location, plus hourly `sats_visible` aggregates precomputed **only for active cells** |
| Open-Meteo | point/hour | fetched per active-cell centroid, keyed `(cell, hour_utc)` |
| M-Lab | client geo (unreliable) | aggregated to **res 4**, keyed `(res4_cell, hour_utc)` |
| RIPE Atlas | probe coords | probe → res-5 cell, keyed `(cell, hour_utc)` |
| WetLinks | 2 fixed sites, 3-min | offline only; joined by timestamp to ERA5 weather for model fitting |
| User measurements | user-declared location | res-5 cell chosen client-side; server never sees coordinates |

**Canonical time bucket: UTC hour** for the feature/label store; raw measurements keep full
timestamps. The 15-second microstructure is *never* stored as gridded data — it is computed
arithmetically at request time (§4.1), because it is a global schedule, not a spatial dataset.

**Memory/storage discipline for the MacBook Air:** never materialize a global dense grid.
The feature store is sparse (active cells only), columnar (Parquet/DuckDB), partitioned by
month. Expected footprint after a year: low single-digit GB. If any step tries to build an
in-memory global raster, that step is wrong.

### 5.4 DuckDB as the offline warehouse (D2 — the instinct was right)

DuckDB is the correct call, not merely acceptable: it reads the Ookla Parquet straight from
S3 (`httpfs`), has the H3 extension, does the quadkey→H3 aggregation in one SQL statement,
and the entire feature-engineering pipeline is columnar scans over a few GB — exactly
DuckDB's sweet spot. No server, lives in a Docker volume, and the warehouse file plus
Parquet marts are the reproducible interface between ingestion and training.
Layout: `data/warehouse.duckdb` + `data/marts/*.parquet` (training matrices, one per model).

### 5.5 Dagster over Airflow (D4 — overruling the instinct)

Airflow is overkill and would actively hurt: scheduler + webserver + metadata DB is 2–4 GB
of RAM to run **six small jobs**, on a 2 GB production host (§8) that also runs the API and
Postgres. The honest resume translation: recruiters filter for "orchestration experience,"
and Dagster demonstrates the same concepts (DAGs, schedules, sensors, retries, backfills)
plus software-defined assets — the more modern framing, and it interviews *better* because
you can explain why you didn't cargo-cult Airflow. Bare cron was considered and rejected
only because backfills and run observability are genuinely needed (M-Lab monthly backfills,
CelesTrak gap recovery).

Dagster runs as **one container** (webserver + daemon, SQLite run/event storage on a volume).
Jobs and schedules:

| Job | Schedule | Writes |
|---|---|---|
| `celestrak_refresh` | every 2 h | raw JSON cache + hourly `orbital_features` for active cells |
| `weather_refresh` | hourly | `(cell, hour)` weather rows, 48 h forecast horizon |
| `atlas_ingest` | daily | RIPE Atlas ping results → Parquet + hourly aggregates |
| `mlab_ingest` | monthly | BigQuery extract → Parquet (res-4 aggregates) |
| `ookla_ingest` | quarterly | context table refresh |
| `train_models` | weekly | model artifacts + eval report; promotes only on eval pass (§6.4) |
| `pg_backup` | nightly | `pg_dump` → S3 `backups/` (Phase 5) |

---

## 6. Modeling — what actually fits (D5)

### 6.1 Reframing the three requirements

The original instinct (gradient-boosted trees + an RNN + an autoencoder) bundles three jobs
with very different data regimes. Decomposed honestly:

1. **Baseline forecast of latency & throughput, including the evening congestion curve** —
   a *tabular supervised* problem on ~10⁵–10⁶ rows of (cell, hour, features). This is GBM
   territory, full stop. Evening congestion is not a sequence-modeling problem at this data
   density; it is a smooth function of local time that a GBM learns from hour-of-day
   features and cell interactions.
2. **The 15-second rough-patch structure** — *not learnable and not worth learning*: it is a
   published, deterministic global schedule (12/27/42/57 s past the UTC minute). Model it as
   a **deterministic overlay**: the UI countdown, plus a fixed expected-spike shape (median
   spike magnitude and duration around reconfig instants) estimated once from WetLinks and
   later re-estimated from user data. An RNN trained to rediscover a known clock is resume
   theater, and no per-second global training data exists to feed it anyway.
3. **Per-user underperformance detection** — an autoencoder needs a corpus of "normal user"
   telemetry that doesn't exist pre-launch (circular cold-start), and its anomaly scores are
   unexplainable to end users. Instead: the baseline model already produces **quantile
   bands**; underperformance is a *statistical test against the band* (§6.4). Interpretable,
   zero extra training, honest.

### 6.2 The models

- **Six LightGBM boosters:** {latency, download_throughput} × quantile α ∈ {0.1, 0.5, 0.9}
  (`objective='quantile'`). Each trains in under a minute at this scale.
- **Features (curated, ~15, no kitchen sink):** hour-of-day (sin/cos), day-of-week,
  local-solar-time offset, precipitation rate (current + 1 h lag + 3 h forecast),
  `sats_visible`, `max_elevation_deg`, **cell latitude** (constellation density varies
  strongly with latitude — expect a top-3 feature), Ookla `terrestrial_baseline_mbps` and
  `devices` (demand proxy), rolling 7-day cell median of the target (where history exists),
  and a data-source indicator.
- **Labels:** M-Lab res-4 hourly aggregates (throughput + minRTT), RIPE Atlas res-5 hourly
  RTT, user measurements res-5 (once they exist), with source-quality sample weights
  (user > atlas > mlab).
- **Rain-fade term:** fit the precipitation response on WetLinks (dense, co-located
  weather), enter it into the global model as a feature transform; document in the model
  card that the curve is calibrated on two European sites (failure mode F6).

### 6.3 Hierarchical fallback (cold-start-proof serving)

Every prediction resolves the best available level:
`res-5 cell (if ≥ N labeled hours) → res-4 parent → res-3 parent → latitude-band global prior`.
Implementation: one global model with cell-level features; at serving time the "rolling cell
median" feature falls back up the hierarchy, and the API response carries which level
answered (`"basis": "cell" | "region" | "latitude_prior"`) so the UI can say "based on
regional data" honestly. This single mechanism makes the product usable everywhere on day
one while getting sharper wherever users cluster.

### 6.4 Anomaly detection ("Dish Doctor")

For a user with ≥ 20 measurements: mark each measurement as below/above the model's q10 for
its (cell, hour). Under the null (healthy dish), below-q10 occurs with p = 0.1. Flag
underperformance when a one-sided binomial test over the last 50 measurements rejects at
p < 0.01, sustained across ≥ 3 distinct hours-of-day (rules out one bad evening). Report
effect size in user terms: "your median download is 34% below the regional expectation for
your conditions." Surface the dish's own obstruction fraction (from the reporter, §4.3) as a
candidate explanation *before* implying faulty hardware (F9).

Model evaluation gate (applies to `train_models` promotion): time-based split (train ≤ month
M, test month M+1); metrics: pinball loss per quantile, empirical q10–q90 coverage in
[78%, 82%], and q50 MAE **must beat a persistence baseline** (same cell, same hour last
week). If it doesn't beat persistence, the features are broken — stop and debug, don't ship.

### 6.5 Why no PyTorch/MPS (D6)

LightGBM is CPU-native; the full training matrix (< 10⁶ rows × ~15 features) trains all six
boosters in minutes on the M-series CPU. MPS pays off when large dense-tensor ops dominate;
nothing here is that. Even the plausible future neural component (§6.6) is < 5M parameters,
where MPS dispatch overhead eats most of the gain. Adding PyTorch would add ~2 GB to Docker
images and a whole dependency surface for zero benefit. This is the definitive answer to
"is MPS actually worth it": no, at any model size this project should reach.

### 6.6 Explicit later-phase option (do NOT build in v1)

If crowdsourced data reaches ~10⁷ per-second rows from ≥ 500 dishes, a small temporal model
(e.g., a 1D-CNN over the 15 s cycle phase) becomes defensible for spike-shape prediction.
That is a Phase 6+ conversation, gated on data volume, and still CPU-trainable.

---

## 7. Serving architecture

### 7.1 Services (docker-compose)

| Service | Image | Role |
|---|---|---|
| `api` | FastAPI + uvicorn (`python:3.12-slim`, arm64) | REST API, WebSocket latency probe, in-process LightGBM inference (artifacts on a volume) |
| `db` | `postgres:16` | serving store |
| `dagster` | custom (shares an `orbitcast-core` base image with `api`) | orchestration (§5.5) |
| `caddy` | `caddy:2` | TLS + reverse proxy (production only) |

Model inference is **in-process in the API** (LightGBM predict is microseconds). No model
server, no Redis, no queue. YAGNI.

### 7.2 Postgres schema (serving only — analytics lives in DuckDB)

```
users(id uuid pk, token_hash text unique, created_at timestamptz, h3_cell bigint)
measurements(id bigserial pk, user_id uuid fk, ts timestamptz, h3_cell bigint,
             source text,                    -- 'reporter' | 'browser'
             latency_ms real, dl_mbps real, ul_mbps real,
             obstruction_pct real, hw_version text)
weather_cache(h3_cell bigint, hour_utc timestamptz, precip_mm_h real,
              cloud_cover_pct real, snow_mm_h real, pk(h3_cell, hour_utc))
forecast_cache(h3_cell bigint, hour_utc timestamptz, metric text,
               q10 real, q50 real, q90 real, basis text, model_version text,
               pk(h3_cell, hour_utc, metric))
model_registry(version text pk, trained_at timestamptz, eval_json jsonb, promoted bool)
```

Indexes: btree on every `h3_cell` column; BRIN on `measurements.ts`. That is the entirety of
the "geospatial database" — the argument against PostGIS, made concrete.

### 7.3 API surface (v1, exhaustive)

```
GET  /v1/skyview?cell={h3}     → sats_visible, max_elevation_deg, seconds_to_reconfig, schedule[]
GET  /v1/forecast?cell={h3}    → 48 h of {hour, latency:{q10,q50,q90}, dl:{...}, basis, weather}
GET  /v1/map?res=4&metric=dl_q50 → cell aggregates for the hex map (server-cached 1 h)
POST /v1/measurements          → batch ingest (auth: bearer anonymous token)
GET  /v1/dish-doctor           → verdict + effect size + evidence (auth)
POST /v1/users                 → mint anonymous token (rate-limited)
WS   /v1/probe                 → browser latency probe
GET  /healthz                  → liveness
```

The client converts typed locations to an H3 cell **client-side** (geocode via a free
geocoder such as Nominatim, respecting its attribution and rate policy
**[VERIFY AT BUILD TIME]**) — the server never receives an address or raw coordinates.

### 7.4 Frontend

Static bundle. Pages: (1) landing + location entry; (2) dashboard: sky view with reconfig
countdown, 48 h forecast chart with q10–q90 band and rough-patch/rain shading, regional hex
map (deck.gl H3 layer); (3) Dish Doctor; (4) **methodology page — write it seriously**: it
is what Reddit will judge and what recruiters will actually read. State every data source,
every limitation (F1–F10), and the basis-labeling scheme.

---

## 8. Hosting & deployment (D10) — the corrected plan

**Where the original AWS plan was naive, stated bluntly:**
1. **The 12-month free tier no longer exists.** AWS accounts created after **July 15, 2025**
   get up to **$200 in credits and a 6-month free plan**, then pay normally. Any plan shaped
   as "free for the first year on RDS + EC2" is built on a discontinued program.
2. **RDS is the wrong spend for a solo project.** Its value is managed HA/failover/patching
   you don't need pre-traction, at ~$15–25/mo forever after credits. Postgres in Docker with
   a named volume + **nightly `pg_dump` to S3** covers the actual risk (losing crowdsourced
   data) for pennies — and operating your own Postgres *is itself* the resume-relevant
   experience.
3. **One micro instance can't hold the stack.** API + Postgres + Dagster + Caddy needs ~2 GB.
4. Unmentioned traps: NAT gateways (~$32/mo — never create one; public subnet + security
   groups), unbounded egress, missing billing alarms.

**The build:**
- **EC2 `t4g.small`** (2 vCPU ARM, 2 GB, ~$12–14/mo on-demand; no reservations), Ubuntu LTS
  arm64, Docker + Compose plugin, 30 GB gp3, public subnet, security group 80/443 open and
  22 restricted to your IP, Elastic IP.
- **S3**: one private bucket — `backups/` (nightly pg_dump, 30-day lifecycle), `models/`
  (LightGBM artifacts + eval reports), `raw/` (Parquet mirrors).
- **Frontend on Vercel free tier** (static; removes CloudFront/S3-website/ACM plumbing).
  CloudFront+S3 is the fallback if Vercel's non-commercial terms become a problem
  **[VERIFY AT BUILD TIME]**.
- **CI/CD**: GitHub Actions — ruff, pyright (or mypy), pytest on PR; multi-arch image build
  → GHCR on main; deploy step SSHes to the instance and runs
  `docker compose pull && docker compose up -d`. No Kubernetes, no ECS — a clean Compose
  deploy with CI reads as stronger judgment than a broken Terraform-EKS cosplay.
- **Day-one guardrails:** AWS Budget alarms at $10 and $25; UptimeRobot (free) on `/healthz`;
  `docker logs` + logrotate for logging — nothing fancier at this scale.
- **DNS**: any registrar; Cloudflare free with proxy **off** (Caddy owns TLS).
- **Honest cost:** ~$15–18/mo once credits are exhausted; the $200 credit covers roughly the
  first year of the EC2 bill if claimed properly.

---

## 9. Failure modes & edge cases registry

Design against these explicitly; several invalidate naive versions of the product.

- **F1 — Ookla is not Starlink data.** (Resolved by D7.) Never present Ookla-derived numbers
  as Starlink baselines anywhere in the UI or docs.
- **F2 — M-Lab geolocation snaps to Starlink PoP cities.** (Resolved: res-4 aggregation +
  `basis` transparency.) Spot-check: a cell with an implausible mass of tests is a PoP city —
  build a PoP-city denylist from published Starlink PoP locations **[VERIFY AT BUILD TIME]**.
- **F3 — You cannot know which satellite serves a user.** Assignment is SpaceX-internal.
  `sats_visible`/elevation are *supply proxies*; all product copy must say "satellites
  overhead," never "your satellite."
- **F4 — The 15 s schedule could change.** It is SpaceX's internal scheduler, documented
  only by measurement papers. Phase 4 data must continuously re-verify spike alignment with
  the 12/27/42/57 clock; ship the countdown with a confidence tag and auto-degrade the UI if
  alignment drops.
- **F5 — CelesTrak throttling/outage.** Stale GP data degrades gracefully (positions drift
  slowly; elevation-count features stay useful for days). Never hot-retry; alert at > 12 h
  stale.
- **F6 — WetLinks is two European sites, one winter season.** The rain-fade curve may not
  transfer to tropical rain rates; label the confidence, re-fit once crowdsourced data
  covers heavy-rain events elsewhere.
- **F7 — Sparse-label regions.** Hierarchical fallback (§6.3) + honest `basis` labeling. The
  map must render *something* everywhere without implying measured data exists there.
- **F8 — Privacy & Reddit optics.** Res-5-only location storage (D12), no IPs, no emails, a
  plain-language privacy page, and the methodology page live **before** launch. Reddit
  moderators remove stealth self-promotion: post as "I built a free tool; here's how it
  works and what it can't do," limitations up front — the limitations section is what earns
  trust. Never scrape or automate Reddit itself.
- **F9 — Anomaly false positives.** A user behind a tree line will flag as underperforming.
  Dish Doctor presents obstruction stats first (§6.4) and phrases verdicts as evidence, not
  accusation.
- **F10 — Single-host fragility.** Postgres volume + nightly S3 dumps + `restart:
  unless-stopped` + a documented 30-minute rebuild runbook is the accepted risk posture at
  this budget. Write `docs/runbook.md` in Phase 5 and validate it once by actually
  destroying and rebuilding the instance.

---

## 10. Execution blueprint — build in this order

Each phase ends with something a real user could use. Do not start a phase before the prior
phase's Definition of Done is met. Use TDD throughout: pytest, failing test first for every
pure function (orbital math, H3 fusion, fallback resolution, binomial test), integration
tests against dockerized Postgres.

### Phase 0 — Skeleton (~half a day)
- [ ] Repo layout: `api/`, `pipelines/` (Dagster), `ml/`, `frontend/`, `infra/`,
      `data/` (gitignored), `docs/`; Python packages managed with `uv`.
- [ ] `docker-compose.yml` with `api` (`GET /healthz` → `{"status":"ok"}`) and `db`; runs
      under OrbStack with `docker compose up`.
- [ ] GitHub Actions: ruff + pyright + pytest on PR; multi-arch image build on main.
- [ ] **DoD:** fresh clone → `docker compose up` → healthz responds; CI green.

### Phase 1 — Sky Dashboard (deterministic, launchable teaser)
- [ ] `celestrak_refresh` fetch-with-cache (respect the 2 h limit; unit-test cache logic
      with a mocked clock; never hit the live API in CI).
- [ ] Orbital engine: skyfield propagation → visible count/elevations for arbitrary lat/lon;
      validate against a known reference pass (skyfield's own examples) in tests.
- [ ] `seconds_to_reconfig` pure function (test the 57 → next-minute-12 wraparound).
- [ ] `GET /v1/skyview`; Open-Meteo now-cast with the per-cell hourly cache.
- [ ] Frontend: location entry → client-side geocode → H3 cell → sky view page with live
      reconfig countdown and satellites-overhead display.
- [ ] **DoD:** a stranger can visit the app, type their town, and watch the countdown tick
      to a real reconfig instant. This alone is shareable.

### Phase 2 — Data spine (ingestion + fusion, no ML yet)
- [ ] Dagster container running `celestrak_refresh` + `weather_refresh` (migrated in), plus
      `atlas_ingest`, `mlab_ingest`, `ookla_ingest` (§5.5), writing Parquet + DuckDB.
- [ ] DuckDB warehouse: quadkey→H3 Ookla aggregation; Atlas/M-Lab hourly marts; WetLinks
      one-time load joined to ERA5 weather by timestamp.
- [ ] Active-cell registry (cells with any label source) driving orbital/weather precompute.
- [ ] **DoD:** one command backfills 90 days of Atlas + the latest M-Lab month + the current
      Ookla quarter into `data/marts/` on the laptop within RAM limits; pipeline tests
      assert row counts and null-rate bounds.

### Phase 3 — Forecast (the ML core)
- [ ] Training-matrix builder in DuckDB SQL → Parquet; deterministic given warehouse state.
- [ ] Six LightGBM quantile boosters + the eval harness and promotion gate from §6.4.
- [ ] Hierarchical fallback resolution with the `basis` field; `train_models` Dagster job
      writing to `model_registry` + S3 on promotion.
- [ ] `GET /v1/forecast` + `forecast_cache`; frontend 48 h chart (uncertainty band, evening
      congestion visible, rain windows shaded) + res-4 hex map endpoint and layer.
- [ ] **DoD:** forecasts render for any global location with honest `basis` labeling; the
      promoted model's eval report is checked into `docs/evals/`.

### Phase 4 — Crowdsourcing + Dish Doctor (the moat)
- [ ] `POST /v1/users` anonymous tokens; `POST /v1/measurements` with validation and rate
      limits; WebSocket browser probe.
- [ ] Dish reporter script + arm64/amd64 Docker image + one-paragraph install docs.
- [ ] Dish Doctor: binomial-test verdict (§6.4), evidence UI, obstruction-first framing.
- [ ] User data enters weekly retraining with source weights; privacy + methodology pages
      written and linked.
- [ ] **DoD:** end-to-end demo — reporter (or a simulated feed) → measurements in Postgres →
      Dish Doctor verdict flips when fed synthetically degraded data.

### Phase 5 — Production + launch
- [ ] EC2 t4g.small provisioned per §8; every step documented in `infra/README.md` (plain
      scripts or minimal Terraform — keep it under ~200 lines or skip IaC entirely).
- [ ] Caddy TLS + domain; GHCR deploy pipeline; nightly `pg_backup` job; budget alarms;
      UptimeRobot.
- [ ] Destroy-and-rebuild drill; write `docs/runbook.md` from what actually happened.
- [ ] Launch checklist: methodology + privacy pages live, limitations section written,
      feedback channel (GitHub issues), Reddit post drafted per F8 norms.
- [ ] **DoD:** public URL, monitored, rebuildable in 30 minutes, ready to post.

---

## 11. Why this architecture is the portfolio argument

For the README and interviews, the claim this project supports — keep it true in the build:
real-time orbital mechanics fused with three independent public measurement networks and
crowdsourced telemetry on a discrete global grid, served by a quantile model that is honest
about its uncertainty and its data provenance, deployed as a containerized system with
CI/CD, orchestration, backups, and a real cost model — with every impressive-sounding
alternative (Airflow, PostGIS, deep nets, RDS, Kubernetes) evaluated and **rejected in
writing, for cause**. The rejections are the senior-engineer signal. Guard them.
