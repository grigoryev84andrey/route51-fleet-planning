"""Checks for the executable dispatch (plan S08)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layer3 import dispatch as dp  # noqa: E402
from layer3 import operations as ops  # noqa: E402
from layer3.optimize import Candidate  # noqa: E402

P = {"cap_small": 90, "cap_large": 150, "veh_cost_small": 1.0, "veh_cost_large": 1.5,
     "route_km": 12.8, "cycle_min": 90, "fleet": 12, "comfort": 0.8,
     "crowding": {"model": "ramp", "theta": 0.8, "lambda_full": 2.0},
     "seg_min": np.array([20.5, 20.5])}


def summary(demand=60.0, hour=7):
    od = pd.DataFrame({"board_stop": ["A", "C"], "alight_stop": ["C", "A"], "hour": [hour, hour],
                       "demand": [demand, demand]})
    return ops.precompute_demand(od, ["A", "B", "C"])


def test_bresenham_example_18_trips():
    pat = dp.large_pattern(18, dp.n_large_for(18, 0.81))
    assert sum(pat) == 15 and len(pat) == 18
    # evenly spread: the three small buses are not adjacent
    small = [k for k, big in enumerate(pat) if not big]
    assert np.diff(small).min() >= 5


@pytest.mark.parametrize("f,want", [(0.0, 0), (1.0, 18), (0.5, 9)])
def test_share_extremes(f, want):
    assert sum(dp.large_pattern(18, dp.n_large_for(18, f))) == want


def test_zero_trips_block():
    assert dp.large_pattern(0, 0) == []


def test_blocks_and_ids():
    trips = dp.build_dispatch([10, 15, 10, 30], [0.81, 0, 0.5, 0], P)
    ids = [t.trip_id for t in trips]
    assert len(set(ids)) == len(ids)
    off = [t for t in trips if t.period == "off_peak"]
    assert all(5 * 3600 <= t.planned_dep_s < 7 * 3600 or 19 * 3600 <= t.planned_dep_s < 23 * 3600
               for t in off)
    am = [t for t in trips if t.period == "am_peak" and t.direction == "fwd"]
    assert len(am) == 18 and sum(t.vtype == "bus18" for t in am) == 15
    assert all(t.cohort for t in am)
    assert not any(t.cohort for t in trips if t.period != "am_peak")


def test_cohort_is_half_open():
    trips = dp.build_dispatch([10, 15, 10, 20], [0, 0, 0, 0], P)
    assert not any(t.cohort and t.planned_dep_s == 10 * 3600 for t in trips)
    assert any(t.cohort and t.planned_dep_s == 7 * 3600 for t in trips)


def test_wait_uniform_matches_integral():
    # 60 pax/hour in am_peak fwd (hour 7 only), headway 10: gaps of 10 min
    s = summary(60.0, 7)
    trips = dp.build_dispatch([10, 15, 10, 20], [0, 0, 0, 0], P)
    w = dp.dispatch_wait(trips, s)
    # both directions: 60 pax in hour 7 each, each waits 5 min on average
    assert w == pytest.approx(2 * 60 * 5.0)


def test_wait_uneven_gap():
    # headway 25, demand only in hour 7 at 1 pax/min: departures 7:00, 7:25, 7:50, 8:15.
    # Arrivals 7:00-7:50 face two full 25-min gaps; arrivals 7:50-8:00 wait until 8:15,
    # i.e. integral of (8:15 - s) over the last 10 minutes = (25^2 - 15^2) / 2.
    s = summary(60.0, 7)
    trips = dp.build_dispatch([25, 15, 10, 20], [0, 0, 0, 0], P)
    w = dp.dispatch_wait(trips, s)
    per_dir = 25 ** 2 / 2 + 25 ** 2 / 2 + (25 ** 2 - 15 ** 2) / 2
    assert w == pytest.approx(2 * per_dir)


def test_cost_and_fleet_and_budget():
    trips = dp.build_dispatch([8, 15, 8, 20], [0, 0, 0, 0], P)
    m = dp.dispatch_metrics(trips, summary(), P)
    n = len(trips)
    assert m["cost"] == pytest.approx(n * 12.8)
    assert m["fleet_needed"] == 12             # 90-min cycle, 8-min headway -> 12 departures
    fz = dp.feasibility(m, budget=m["cost"] - 1, delta=10, fleet=12)
    assert not fz["feasible"] and not fz["checks"]["cost_within_budget"]
    assert fz["operational_feasibility"] == "unknown"


def test_select_executable_walks_and_reports_none():
    s = summary()
    good = Candidate("42:1", np.array([10., 15, 10, 20]), np.zeros(4), 1.0, 1.0, 1.0)
    bad = Candidate("42:0", np.array([7.5, 7.5, 7.5, 7.5]), np.ones(4), 0.5, 1.0, 0.5)
    c, trips, m, log = dp.select_executable([bad, good], s, P, budget=1e9, delta=10)
    # 7.5 min on a 90-min cycle needs 12 vehicles: feasible for fleet 12 -> first wins
    assert c.cid == "42:0" and log[0]["feasible"]
    c, trips, m, log = dp.select_executable([bad, good], s, P, budget=1.0, delta=10)
    assert c is None and len(log) == 2 and not any(r["feasible"] for r in log)


def test_invalid_plan_rejected():
    with pytest.raises(ValueError):
        dp.build_dispatch([0, 15, 10, 20], [0, 0, 0, 0], P)
    with pytest.raises(ValueError):
        dp.build_dispatch([10, 15, 10, 20], [1.2, 0, 0, 0], P)
