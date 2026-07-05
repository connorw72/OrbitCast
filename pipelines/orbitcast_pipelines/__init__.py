"""OrbitCast ingestion + fusion pipelines (CLAUDE.md §5).

Every source reduces to (h3_cell, time_bucket, features...) in a sparse, columnar
DuckDB warehouse + Parquet marts. Dagster orchestrates the jobs (§5.5).
"""

__version__ = "0.1.0"
