"""Map service: aggregates for the regional hex map GET /v1/map (§7.3, §7.4).

The map answers "what does the forecast look like across the cells where we have
any signal, right now." For each active res-5 cell the route runs the in-process
forecast for the current hour, takes the requested quantile of the requested
metric, and this module aggregates those values up to the requested H3 resolution
(res-4 by default). Every aggregated cell carries the *best* provenance among its
children (`basis`) so the UI never implies measured data where there is a prior.

Pure logic lives here; the per-cell inference orchestration lives in the route.
"""

from collections.abc import Iterable, Mapping
from pathlib import Path
from statistics import fmean

import h3
from orbitcast_core.spatial import RESOLUTION
from orbitcast_ml.fallback import Basis

from .forecast import _read_mart_rows

# Payload metric keys (assemble_payload emits "latency" and "dl") and quantile
# keys the /v1/forecast band exposes.
_TARGETS = {"dl", "latency"}
_QUANTS = {"q10", "q50", "q90"}

# Provenance precedence: a measured cell beats a regional roll-up beats a prior
# (§6.3). Aggregated cells report the strongest basis any child carried.
_BASIS_RANK = {"cell": 2, "region": 1, "latitude_prior": 0}

# Marts that pin down where we have any Starlink signal at res 5.
_ACTIVE_MARTS = ("cell_label_stats.parquet", "ookla_context.parquet")


def parse_metric(metric: str) -> tuple[str, str]:
    """Split e.g. ``"dl_q50"`` into ``("dl", "q50")``; raise ValueError otherwise."""
    target, sep, quant = metric.rpartition("_")
    if not sep or target not in _TARGETS or quant not in _QUANTS:
        raise ValueError(f"unknown metric {metric!r}")
    return target, quant


def best_basis(bases: Iterable[Basis]) -> Basis:
    """The strongest provenance among ``bases`` (cell > region > latitude_prior)."""
    return max(bases, key=lambda b: _BASIS_RANK.get(b, -1))


def aggregate_to_res(per_cell: Mapping[int, tuple[float, Basis]], res: int) -> list[dict]:
    """Group res-5 ``{cell: (value, basis)}`` under their res-``res`` parents.

    Each output cell carries the mean child value, the best child basis, and the
    child count ``n``. Sorted by cell id for a stable payload.
    """
    groups: dict[int, list[tuple[float, Basis]]] = {}
    for cell, (value, basis) in per_cell.items():
        hexid = h3.int_to_str(cell)
        if h3.get_resolution(hexid) <= res:
            parent = cell
        else:
            parent = h3.str_to_int(h3.cell_to_parent(hexid, res))
        groups.setdefault(parent, []).append((value, basis))

    out = [
        {
            "cell": parent,
            "value": fmean(v for v, _ in items),
            "basis": best_basis(b for _, b in items),
            "n": len(items),
        }
        for parent, items in groups.items()
    ]
    out.sort(key=lambda c: c["cell"])
    return out


def active_map_cells(marts_dir: Path) -> set[int]:
    """Res-5 cells with any label source or Ookla context — the map's canvas.

    Only res-5 keys are taken; the label marts also hold res-4 roll-ups (M-Lab),
    which would double-count under aggregation, so they are filtered out here.
    """
    cells: set[int] = set()
    for name in _ACTIVE_MARTS:
        for row in _read_mart_rows(marts_dir / name):
            raw = row.get("h3_cell")
            if raw is None:
                continue
            cell = int(raw)
            if h3.get_resolution(h3.int_to_str(cell)) == RESOLUTION:
                cells.add(cell)
    return cells
