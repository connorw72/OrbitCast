-- OrbitCast serving-store schema (CLAUDE.md §7.2).
--
-- This is the *entire* geospatial database: H3 res-5 cells stored as BIGINT turn
-- every location query into a btree lookup, which is why there is no PostGIS (D3).
-- Analytics live in DuckDB; only serving state lives here.
--
-- Applied idempotently at API startup (CREATE ... IF NOT EXISTS), so a fresh
-- Postgres volume self-migrates. No ORM, no Alembic — the schema is small enough
-- to own as plain SQL.

-- Anonymous users: no email, no signup. A user is a random bearer token; we store
-- only its SHA-256 hash (D12), so a DB leak cannot impersonate anyone. h3_cell is
-- the res-5 cell the user declared client-side — never raw coordinates (~250 km²).
CREATE TABLE IF NOT EXISTS users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    h3_cell     BIGINT
);

-- Crowdsourced measurements (§4.3): dish reporter or browser probe. Latency only
-- for the browser path; the reporter also carries throughput, obstruction, and
-- hardware version. Locations are res-5 cells chosen client-side (D12).
CREATE TABLE IF NOT EXISTS measurements (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users (id),
    ts              TIMESTAMPTZ NOT NULL,
    h3_cell         BIGINT NOT NULL,
    source          TEXT NOT NULL,          -- 'reporter' | 'browser'
    latency_ms      REAL,
    dl_mbps         REAL,
    ul_mbps         REAL,
    obstruction_pct REAL,
    hw_version      TEXT
);

-- Btree on every h3_cell column; BRIN on the append-only measurement timestamp
-- (§7.2). That is the whole index story.
CREATE INDEX IF NOT EXISTS idx_users_h3_cell ON users (h3_cell);
CREATE INDEX IF NOT EXISTS idx_measurements_h3_cell ON measurements (h3_cell);
CREATE INDEX IF NOT EXISTS idx_measurements_user_id ON measurements (user_id);
CREATE INDEX IF NOT EXISTS idx_measurements_ts_brin ON measurements USING BRIN (ts);
