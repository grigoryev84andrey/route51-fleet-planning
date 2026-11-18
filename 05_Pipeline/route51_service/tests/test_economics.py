"""Checks for resources and scoped economics (plan S10)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layer3.dispatch import Trip  # noqa: E402
from layer3.resources import resources  # noqa: E402
from layer5 import economics as eco  # noqa: E402

P = {"veh_cost_small": 2.0, "veh_cost_large": 3.4}          # r = 1.7, cost_small != 1


def trip(vtype, per="am_peak"):
    big = vtype == "bus18"
    return Trip(f"x.{vtype}.{per}", per, 0, "fwd", 0, 25200, vtype, 150 if big else 90,
                3.4 if big else 2.0, 10.0, True)


def test_one_small_one_large_same_length():
    r = resources([trip("bus12"), trip("bus18")], P, one_way_min=41, layover_min=4)
    assert r["veh_km"] == 20 and r["veh_km_18m"] == 10
    assert r["cost_ratio_r"] == pytest.approx(1.7)
    assert r["equiv_12m_km"] == pytest.approx(10 + 1.7 * 10)
    assert r["cost_index"] == pytest.approx(2.0 * 27.0)
    assert r["capacity_km"] == 10 * 90 + 10 * 150
    assert r["normative_veh_h"] == pytest.approx(2 * 45 / 60)
    am = resources([trip("bus12"), trip("bus18", "midday")], P, one_way_min=41, layover_min=4,
                   periods=["am_peak"])
    assert am["trips"] == 1


def block(**kw):
    base = dict(scope="am_peak", wait_base=100.0, wait_plan=160.0, crowd_base=500.0, crowd_plan=200.0,
                K_base=100.0, K_plan=110.0, ivt_benefit_pax_min=None, ivt_reason="no_simulation",
                rate_12m=None, days=250, vot=60.0)
    base.update(kw)
    return eco.scope_block(**base)


def test_unknown_rate_and_ivt_are_null_not_zero():
    b = block()["by_wait_weight"]["wait_weight_1"]
    assert b["passenger_model_uzs"] == pytest.approx((-60 + 300) * 250 / 60 * 60)
    assert b["operator_delta_cost_uzs"] is None and b["operator_delta_cost_reason"] == "no_sourced_rate"
    assert b["passenger_with_ivt_uzs"] is None and b["combined_uzs"] is None
    b0 = block(rate_12m=0.0)["by_wait_weight"]["wait_weight_1"]
    assert b0["operator_delta_cost_uzs"] == 0.0          # zero rate is a different state


def test_wait_weight_two_and_no_factor_ten():
    b2 = block()["by_wait_weight"]["wait_weight_2"]
    assert b2["wait_benefit_h"] == pytest.approx(2 * -60 * 250 / 60)
    assert b2["crowd_benefit_h"] == pytest.approx(300 * 250 / 60)


def test_combined_when_everything_known():
    b = block(ivt_benefit_pax_min=30.0, rate_12m=1000.0)["by_wait_weight"]["wait_weight_1"]
    pw = ((-60 + 300) + 30) * 250 / 60 * 60
    assert b["passenger_with_ivt_uzs"] == pytest.approx(pw)
    assert b["combined_uzs"] == pytest.approx(pw - 1000.0 * 10 * 250)


@pytest.mark.parametrize("B,dK,kind", [(10, 2, "max_rate"), (0, 2, "only_zero"), (-1, 2, "impossible"),
                                       (5, 0, "no_division"), (5, -2, "any_rate"), (-4, -2, "min_rate"),
                                       (None, 2, "unknown"), (1, None, "unknown")])
def test_rate_threshold_signs(B, dK, kind):
    t = eco.rate_threshold(B, dK)
    assert t["kind"] == kind
    if kind in ("max_rate", "min_rate"):
        assert t["value"] == pytest.approx(B / dK)


def test_simulation_ivt_requires_applicable_confirmatory():
    fps = {"input": "i", "plan_baseline": "b", "plan_candidate": "c",
           "model": "m", "protocol": "p", "counts": {"baseline": 30, "candidate": 30}}
    sim = {"series": "technical", "evidence_kind": "exploratory", "completion_status": "complete",
           "schema_version": 2, "protocol_status": "passed", "effect_status": "improved",
           "primary": {"defined": True, "n_baseline": 30, "n_candidate": 30},
           "invalid_runs": [], "fingerprints": {"input": "i", "plan_baseline": "b",
                                               "plan_candidate": "c", "model": "m", "protocol": "p"},
           "secondary_exploratory": {"pax_ivt_am_min": {"baseline": 100.0, "candidate": 90.0}}}
    assert eco.simulation_ivt(sim, fps)[0] is None
    sim.update(series="confirmatory", evidence_kind="confirmatory")
    assert eco.simulation_ivt(sim, fps)[0] == pytest.approx(10.0)
    assert eco.simulation_ivt(sim, {**fps, "plan_candidate": "other"})[1] == "simulation_not_applicable"
    assert eco.simulation_ivt(sim, {**fps, "model": "other"})[1] == "simulation_not_applicable"
    assert eco.simulation_ivt(None, fps)[1] == "no_simulation"


def test_value_of_time_matches_published_basis():
    assert eco.VALUE_OF_TIME_UZS_H == pytest.approx(
        0.45 * ((11_684_262.5 + 12_013_894.7) / 2) * 0.88 / 168)
