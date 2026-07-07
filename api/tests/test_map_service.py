"""Pure map-service logic: metric parsing, basis precedence, res aggregation.

These are the honest-provenance and grouping rules behind GET /v1/map (§7.3,
§7.4). No network, no model, no marts — just the arithmetic.
"""

import h3
import pytest
from orbitcast_api.map import aggregate_to_res, best_basis, parse_metric


def test_parse_metric_splits_target_and_quantile():
    assert parse_metric("dl_q50") == ("dl", "q50")
    assert parse_metric("latency_q10") == ("latency", "q10")
    assert parse_metric("latency_q90") == ("latency", "q90")


@pytest.mark.parametrize("bad", ["dl", "dl_q99", "upload_q50", "q50", "dl_50", ""])
def test_parse_metric_rejects_unknown(bad):
    with pytest.raises(ValueError):
        parse_metric(bad)


def test_best_basis_prefers_measured_over_prior():
    assert best_basis(["latitude_prior", "region", "cell"]) == "cell"
    assert best_basis(["latitude_prior", "region"]) == "region"
    assert best_basis(["latitude_prior"]) == "latitude_prior"


def _res5_children(lat: float, lon: float, n: int) -> list[int]:
    """`n` distinct res-5 cells sharing one res-4 parent, as BIGINT ids."""
    parent = h3.cell_to_parent(h3.latlng_to_cell(lat, lon, 5), 4)
    kids = sorted(h3.cell_to_children(parent, 5))[:n]
    return [h3.str_to_int(k) for k in kids]


def test_aggregate_groups_res5_children_under_res4_parent():
    a, b = _res5_children(52.28, 8.05, 2)
    parent4 = h3.str_to_int(h3.cell_to_parent(h3.int_to_str(a), 4))

    out = aggregate_to_res({a: (100.0, "region"), b: (140.0, "latitude_prior")}, res=4)

    assert len(out) == 1
    cell = out[0]
    assert cell["cell"] == parent4
    assert cell["value"] == pytest.approx(120.0)  # mean of the two children
    assert cell["n"] == 2
    assert cell["basis"] == "region"  # best available among children


def test_aggregate_separates_distinct_parents_and_sorts():
    a, b = _res5_children(52.28, 8.05, 2)
    (c,) = _res5_children(-33.9, 151.2, 1)  # Sydney — a different res-4 parent

    out = aggregate_to_res({a: (10.0, "cell"), b: (20.0, "cell"), c: (5.0, "region")}, res=4)

    assert len(out) == 2
    assert [cc["cell"] for cc in out] == sorted(cc["cell"] for cc in out)
    by_cell = {cc["cell"]: cc for cc in out}
    parent_ab = h3.str_to_int(h3.cell_to_parent(h3.int_to_str(a), 4))
    parent_c = h3.str_to_int(h3.cell_to_parent(h3.int_to_str(c), 4))
    assert by_cell[parent_ab]["value"] == pytest.approx(15.0)
    assert by_cell[parent_ab]["n"] == 2
    assert by_cell[parent_c]["n"] == 1


def test_aggregate_res5_is_identity_grouping():
    (a,) = _res5_children(52.28, 8.05, 1)
    out = aggregate_to_res({a: (42.0, "cell")}, res=5)
    assert out == [{"cell": a, "value": 42.0, "basis": "cell", "n": 1}]
