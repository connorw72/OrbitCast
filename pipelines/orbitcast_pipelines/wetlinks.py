"""WetLinks rain-fade dataset — one-time offline load (CLAUDE.md D7, §4.2c).

Six months (Oct 2023 - Mar 2024), two European sites (Osnabrueck DE, Enschede NL),
~140k rows every 3 minutes: throughput, RTT, loss + co-located weather. Role: fit
the precipitation -> performance response used as a feature transform (Phase 3),
and sanity-check the 15 s periodic structure. NOT a live source — never joined at
serving time as current data (F6: two sites, one winter; label the confidence).

Source: TMA 2024 paper "WetLinks" (arXiv:2402.16448); dataset link is in the paper
[VERIFY AT BUILD TIME]. Download once to data/raw/wetlinks/ and load here.
"""

from pathlib import Path

import duckdb


def load_wetlinks(con: duckdb.DuckDBPyConnection, raw_dir: Path) -> list[dict]:
    """Load the downloaded WetLinks CSVs into rows. Offline; expects files under
    `raw_dir`. Timestamp alignment with ERA5 weather happens in the Phase 3
    training-matrix build."""
    files = sorted(Path(raw_dir).glob("*.csv"))
    if not files:
        raise FileNotFoundError(
            f"No WetLinks CSVs in {raw_dir}. Download from arXiv:2402.16448 first."
        )
    glob = str(Path(raw_dir) / "*.csv")
    return con.execute(f"SELECT * FROM read_csv_auto('{glob}')").fetchall()  # type: ignore[return-value]
