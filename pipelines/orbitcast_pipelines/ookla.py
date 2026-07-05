"""Ookla open-data ingest: zoom-16 tiles -> H3 res-5 context (CLAUDE.md §4.2d).

No ISP breakdown (F1): this is context only — a terrestrial baseline and a demand
proxy per cell, never presented as a Starlink number. Read straight from the
public S3 bucket over HTTPS with DuckDB httpfs; aggregate tiles to res-5 by
converting each tile centroid with h3, test-weighted.
"""

import duckdb

# Public Ookla bucket; quarterly fixed-broadband tiles.
_URL_TEMPLATE = (
    "https://ookla-open-data.s3.amazonaws.com/parquet/performance/"
    "type=fixed/year={year}/quarter={quarter}/{date}_performance_fixed_tiles.parquet"
)


def quarter_url(year: int, quarter: int) -> str:
    month = {1: "01", 2: "04", 3: "07", 4: "10"}[quarter]
    return _URL_TEMPLATE.format(year=year, quarter=quarter, date=f"{year}-{month}-01")


def aggregate_ookla_to_h3(con: duckdb.DuckDBPyConnection, source: str) -> list[dict]:
    """Aggregate an Ookla tile source (a table name or ``read_parquet('...')``)
    into H3 res-5 cells. Returns one dict per cell."""
    sql = f"""
    WITH tiled AS (
        SELECT
            h3_latlng_to_cell(
                ST_Y(ST_Centroid(ST_GeomFromText(tile))),
                ST_X(ST_Centroid(ST_GeomFromText(tile))),
                5
            ) AS h3_cell,
            avg_d_kbps, avg_lat_ms, tests, devices
        FROM {source}
    )
    SELECT
        h3_cell,
        SUM(tests) AS tests,
        SUM(devices) AS devices,
        SUM(avg_d_kbps * tests) / SUM(tests) / 1000.0 AS terrestrial_baseline_mbps,
        SUM(avg_lat_ms * tests) / SUM(tests) AS terrestrial_latency_ms
    FROM tiled
    GROUP BY h3_cell
    """
    cur = con.execute(sql)
    columns = [c[0] for c in cur.description]
    return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
