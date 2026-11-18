"""Checks for the schema-2 result contract (plan S03, «Проверки»)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import result_contracts as rc  # noqa: E402

OD = pd.DataFrame({"board_stop": ["S01", "S01", "S02"], "alight_stop": ["S02", "S03", "S03"],
                   "hour": [7, 7, 8], "demand": [1.25, 0.1, 3.0]})
CFG = {"demand_scale": 3, "crowding": {"model": "ramp", "threshold": 0.8}}
PLAN_A = {"periods": [{"period": "am_peak", "headway_min": 10.1, "share_18m": 0.81}]}
PLAN_B = {"periods": [{"period": "am_peak", "headway_min": 10.2, "share_18m": 0.81}]}
PROTO = {"primary": "mean_full_trip_duration", "alpha": 0.05, "n": 30}


def confirmed(**over):
    fps = {"input": rc.input_fingerprint(OD, CFG), "plan": rc.plan_fingerprint(PLAN_A),
           "model": rc.model_fingerprint({"sumo": "1.27.0"}),
           "protocol": rc.protocol_fingerprint(PROTO), "run": None}
    r = rc.new_result(input_match="exact", plan_match="exact", evidence_kind="confirmatory",
                      execution_state="completed", completion_status="complete",
                      protocol_status="passed", effect_status="improved", fingerprints=fps)
    r.update(over)
    return r


def check(result, **kw):
    args = dict(expected_input_fp=rc.input_fingerprint(OD, CFG),
                expected_plan_fp=rc.plan_fingerprint(PLAN_A),
                expected_model_fp=rc.model_fingerprint({"sumo": "1.27.0"}),
                expected_protocol_fp=rc.protocol_fingerprint(PROTO),
                registered_confirmatory=True)
    args.update(kw)
    return rc.validate_result(result, **args)


def codes(v):
    return {i["code"] for i in v["validation_issues"]}


# --- applicability ---------------------------------------------------------
def test_full_valid_result_applies():
    v = check(confirmed())
    assert v["applicable"] and v["input_match"] == "exact" and v["plan_match"] == "exact"


def test_missing_simulation():
    v = check(None)
    assert not v["applicable"] and codes(v) == {"no_result"}


def test_other_plan():
    v = check(confirmed(), expected_plan_fp=rc.plan_fingerprint(PLAN_B))
    assert not v["applicable"] and v["plan_match"] == "different" and "plan_different" in codes(v)


def test_other_input():
    other = OD.assign(demand=OD.demand * 2)
    v = check(confirmed(), expected_input_fp=rc.input_fingerprint(other, CFG))
    assert not v["applicable"] and v["input_match"] == "different"


def test_other_model():
    v = check(confirmed(), expected_model_fp=rc.model_fingerprint({"sumo": "other"}))
    assert not v["applicable"] and "model_different" in codes(v)


def test_analysis_rejects_mixed_revision_and_missing_runs():
    fps = {"input": "i", "plan_baseline": "b", "plan_candidate": "c",
           "model": "m", "protocol": "p"}
    analysis = {"schema_version": 2, "series": "confirmatory", "evidence_kind": "confirmatory",
                "completion_status": "complete", "protocol_status": "passed",
                "effect_status": "improved", "fingerprints": dict(fps), "invalid_runs": [],
                "primary": {"defined": True, "n_baseline": 30, "n_candidate": 30}}
    def verdict(a):
        return rc.validate_simulation_analysis(a, expected_fps=fps,
                                               expected_counts={"baseline": 30, "candidate": 30})
    assert verdict(analysis)["applicable"]
    for key, value, reason in (("model", "old", "model_different"),
                               ("protocol", "old", "protocol_different"),
                               ("plan_baseline", "old", "baseline_different")):
        bad = {**analysis, "fingerprints": {**fps, key: value}}
        assert reason in codes(verdict(bad)) and not verdict(bad)["applicable"]
    bad = {**analysis, "primary": {"defined": True, "n_baseline": 29, "n_candidate": 30}}
    assert "cohort_count_different" in codes(verdict(bad))
    bad = {**analysis, "invalid_runs": [{"seed": 1}]}
    assert "invalid_runs_present" in codes(verdict(bad))


def test_incomplete_series():
    v = check(confirmed(completion_status="incomplete"))
    assert not v["applicable"] and "incomplete_cohort" in codes(v)


def test_completed_job_with_failed_protocol():
    v = check(confirmed(protocol_status="failed"))
    assert not v["applicable"] and "protocol_failed" in codes(v)


def test_valid_negative_effect_still_applies():
    v = check(confirmed(effect_status="worsened"))
    assert v["applicable"] and v["effect_status"] == "worsened"


def test_exploratory_and_unregistered_do_not_apply():
    assert "not_confirmatory" in codes(check(confirmed(evidence_kind="exploratory")))
    assert "not_registered" in codes(check(confirmed(), registered_confirmatory=False))


def test_interrupted_and_bad_status():
    assert "not_completed" in codes(check(confirmed(execution_state="interrupted")))
    assert "bad_status_value" in codes(check(confirmed(effect_status="great")))


def test_legacy_result_is_never_applicable():
    old = {"mode": "archive", "applies": True, "run_time": {"diff_min": 1.89}}
    wrapped = rc.from_legacy_simulation(old, similar_note="within 0.3 min")
    assert wrapped["evidence_kind"] == "historical_reference"
    v = check(wrapped)
    assert not v["applicable"] and "legacy_schema" in codes(v)
    # the raw old dict is rejected outright
    assert codes(check(old)) == {"schema_unsupported"}


# --- fingerprints ----------------------------------------------------------
def test_od_row_order_does_not_change_input_fp():
    shuffled = OD.iloc[[2, 0, 1]].reset_index(drop=True)
    assert rc.input_fingerprint(OD, CFG) == rc.input_fingerprint(shuffled, CFG)


def test_full_precision_and_config_change_input_fp():
    tiny = OD.assign(demand=OD.demand + np.array([1e-12, 0, 0]))
    assert rc.input_fingerprint(OD, CFG) != rc.input_fingerprint(tiny, CFG)
    cfg2 = {**CFG, "crowding": {"model": "ramp", "threshold": 0.9}}
    assert rc.input_fingerprint(OD, CFG) != rc.input_fingerprint(OD, cfg2)


def test_route_order_changes_plan_fp():
    a = {"route": ["S01", "S02", "S03"]}
    b = {"route": ["S01", "S03", "S02"]}
    assert rc.plan_fingerprint(a) != rc.plan_fingerprint(b)


def test_kinds_are_separated_and_sets_rejected():
    assert rc.plan_fingerprint({"x": 1}) != rc.model_fingerprint({"x": 1})
    with pytest.raises(TypeError):
        rc.plan_fingerprint({"stops": {"S01", "S02"}})
    with pytest.raises(ValueError):
        rc.plan_fingerprint({"h": float("nan")})


def test_numpy_scalars_match_python_values():
    assert rc.plan_fingerprint({"h": np.float64(10.1), "n": np.int64(3)}) == \
        rc.plan_fingerprint({"h": 10.1, "n": 3})


def test_file_hash_is_separate(tmp_path):
    p = tmp_path / "od.csv"
    OD.to_csv(p, index=False)
    assert len(rc.file_sha256(p)) == 64
    assert rc.file_sha256(p) != rc.input_fingerprint(OD, CFG).split(":")[-1]


def test_duplicate_od_keys_rejected():
    with pytest.raises(ValueError):
        rc.input_fingerprint(pd.concat([OD, OD.head(1)]), CFG)


# --- unknown values and effect rule ---------------------------------------
def test_unknown_is_null_not_zero():
    u = rc.unknown("no_result")
    assert u["value"] is None and u["reason_code"] == "no_result" and u["message"]


@pytest.mark.parametrize("lo,hi,passed,want", [
    (0.1, 0.5, True, "improved"),
    (-0.5, -0.1, True, "worsened"),
    (-0.1, 0.3, True, "inconclusive"),
    (0.1, 0.5, False, "not_tested"),
    (None, None, True, "not_tested"),
])
def test_classify_effect(lo, hi, passed, want):
    assert rc.classify_effect(lo, hi, protocol_passed=passed) == want
