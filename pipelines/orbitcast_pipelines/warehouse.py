"""DuckDB warehouse + Parquet marts (CLAUDE.md D2, §5.4).

Single warehouse file in a Docker volume; Parquet marts are the reproducible
interface between ingestion and training. The feature store is sparse (active
cells only) and columnar — never materialize a global dense grid (§5.3).
"""

from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

DATA_DIR = Path("data")
WAREHOUSE = DATA_DIR / "warehouse.duckdb"
MARTS = DATA_DIR / "marts"

# h3 (community) for spatial keys, spatial for Ookla tile geometry, httpfs for
# reading Ookla parquet straight from the public S3 bucket over HTTPS.
_COMMUNITY = ("h3",)
_CORE = ("spatial", "httpfs")


def load_extensions(con: duckdb.DuckDBPyConnection) -> None:
    for ext in _COMMUNITY:
        con.execute(f"INSTALL {ext} FROM community; LOAD {ext};")
    for ext in _CORE:
        con.execute(f"INSTALL {ext}; LOAD {ext};")


def connect(path: Path | str = WAREHOUSE, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    load_extensions(con)
    return con


def marts_dir() -> Path:
    MARTS.mkdir(parents=True, exist_ok=True)
    return MARTS


def write_mart(rows: list[dict], path: Path | str) -> Path:
    """Write mart rows to Parquet. Skips writing when there are no rows."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return path
    pq.write_table(pa.Table.from_pylist(rows), str(path))
    return path


def read_mart(path: Path | str) -> list[dict]:
    return pq.read_table(str(path)).to_pylist()
