"""Numerical checks of the crowding model (plan S06)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layer3 import operations as ops  # noqa: E402

CROWD = {"model": "ramp", "theta": 0.8, "lambda_full": 2.0}


def terms(L, n, c, t, **over):
    return ops.crowding_terms(np.array([L], float), n, c, np.array([t], float), {**CROWD, **over})


def test_ramp_and_step_worked_example():
    # L=90, n=1, c=100, t=10: phi = 0.9
    assert terms(90, 1, 100, 10)["crowd_min"] == pytest.approx(450.0)
    assert terms(90, 1, 100, 10, model="step")["crowd_min"] == pytest.approx(900.0)


def test_ramp_at_and_above_full_capacity():
    lam = ops.crowding_multiplier([1.0, 1.2], "ramp", 0.8, 2.0)
    assert lam == pytest.approx([2.0, 3.0])


def test_none_and_unit_multiplier_give_zero():
    assert terms(90, 1, 100, 10, model="none")["crowd_min"] == 0.0
    assert terms(90, 1, 100, 10, lambda_full=1.0)["crowd_min"] == 0.0


def test_below_threshold_no_crowding_and_units():
    r = terms(70, 1, 100, 10)
    assert r["crowd_min"] == 0.0 and r["ivt_min"] == pytest.approx(700.0)


def test_over_capacity_flag():
    assert terms(120, 1, 100, 10)["over_capacity_segments"] == 1


def test_segment_times_sum_to_norm():
    stops = ["A", "B", "C", "D"]
    ll = {"A": (41.30, 69.24), "B": (41.31, 69.25), "C": (41.31, 69.27), "D": (41.33, 69.28)}
    t = ops.segment_minutes(stops, ll, 41.0)
    assert t.sum() == pytest.approx(41.0) and (t > 0).all()
    with pytest.raises(ValueError):
        ops.segment_minutes(stops, {s: (41.3, 69.2) for s in stops}, 41.0)
    assert ops.segment_minutes(stops, None, 41.0, "uniform") == pytest.approx([41 / 3] * 3)


def _params(**crowd):
    c = {**CROWD, **crowd}
    return {"cap_small": 90, "cap_large": 150, "veh_cost_small": 1.0, "veh_cost_large": 1.5,
            "route_km": 12.8, "cycle_min": 90, "comfort": c["theta"], "crowding": c,
            "seg_min": np.array([20.5, 20.5])}


def test_zero_demand_share_is_null():
    od = pd.DataFrame({"board_stop": ["A"], "alight_stop": ["B"], "hour": [7], "demand": [0.0]})
    s = ops.precompute_demand(od, ["A", "B", "C"])
    m = ops.evaluate_plan([10] * 4, [0] * 4, s, _params())
    assert m["crowded_ivt_share"] is None and m["crowded_ivt_share_reason"] == "no_in_vehicle_time"


def test_demand_errors_instead_of_silent_drop():
    stops = ["A", "B", "C"]
    good = {"board_stop": ["A"], "alight_stop": ["C"], "hour": [7], "demand": [1.0]}
    for bad in ({"hour": [3]}, {"board_stop": ["Z"]}, {"demand": [-1.0]},
                {"alight_stop": ["A"]}, {"demand": [float("nan")]}):
        with pytest.raises(ops.DemandError):
            ops.precompute_demand(pd.DataFrame({**good, **bad}), stops)


def test_full_profile_and_reverse_order():
    od = pd.DataFrame({"board_stop": ["A", "C"], "alight_stop": ["C", "B"], "hour": [7, 7],
                       "demand": [10.0, 4.0]})
    s = ops.precompute_demand(od, ["A", "B", "C"])
    assert s["am_peak"]["fwd"]["load"] == pytest.approx([10, 10])
    assert s["am_peak"]["rev"]["load"] == pytest.approx([0, 4])
    assert s["am_peak"]["rev"]["load_travel_order"] == pytest.approx([4, 0])
    assert s["_total_demand"] == pytest.approx(14.0)


def test_theta_conflict_is_error():
    cfg = {"crowding": {"model": "ramp", "threshold": 0.8, "multiplier_at_full_capacity": 2.0},
           "operations": {"comfort_load_factor": 0.7}}
    with pytest.raises(ValueError):
        ops.crowding_config(cfg)
    cfg["operations"]["comfort_load_factor"] = 0.8
    assert ops.crowding_config(cfg)["version"] == "ivt_multiplier_v1"
    for bad in ({"model": "cubic"}, {"threshold": 1.0}, {"multiplier_at_full_capacity": 0.5}):
        with pytest.raises(ValueError):
            ops.crowding_config({**cfg, "crowding": {**cfg["crowding"], **bad}})


def test_no_hidden_factor_ten():
    od = pd.DataFrame({"board_stop": ["A"], "alight_stop": ["C"], "hour": [7], "demand": [900.0]})
    s = ops.precompute_demand(od, ["A", "B", "C"])
    m = ops.evaluate_plan([10] * 4, [0] * 4, s, _params())
    # am_peak fwd: n = 18 trips, c = 90, L = 900 -> phi = 0.5556 < 0.8 -> no crowding
    assert m["crowd_min"] == 0.0
    m = ops.evaluate_plan([30] * 4, [0] * 4, s, _params())
    # n = 6, phi = 900/540 = 1.6667, lambda = 1 + (1.6667-0.8)/0.2 = 5.3333
    lam = 1 + (900 / 540 - 0.8) / 0.2
    assert m["crowd_min"] == pytest.approx((lam - 1) * 900 * 41.0)
    assert m["gen_cost"] == pytest.approx(m["wait"] + m["crowd_min"])
