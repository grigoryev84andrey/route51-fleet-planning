"""Checks for the simulation experiment (plan S09). SUMO itself is not called here;
the integration smoke run is recorded in EXECUTION_LOG (S09)."""
import gzip
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layer3.dispatch import Trip  # noqa: E402
from layer4 import background  # noqa: E402
from layer4.analysis import analyse, welch  # noqa: E402
from layer4.experiment import parse_run, run_outcome, stop_activity, write_bus_routes  # noqa: E402


def trip(tid, d, dep, cohort=True, vtype="bus12", period="am_peak"):
    return Trip(tid, period, 0, d, 0, dep, vtype, 90 if vtype == "bus12" else 150, 1.0, 12.8, cohort)


def _write(wd, tag, tripinfos, stops=(), log=""):
    ti = "<tripinfos>" + "".join(
        f'<tripinfo id="{i}" depart="{dep}" departDelay="{dd}" arrival="{arr}" duration="{dur}" vType="bus12"/>'
        for i, dep, dd, arr, dur in tripinfos) + "</tripinfos>"
    with gzip.open(wd / f"{tag}.ti.xml.gz", "wb") as fh:
        fh.write(ti.encode())
    st = "<stops>" + "".join(
        f'<stopinfo id="{i}" busStop="{b}" started="{s}" ended="{e}"/>' for i, b, s, e in stops) + "</stops>"
    with gzip.open(wd / f"{tag}.stops.xml.gz", "wb") as fh:
        fh.write(st.encode())
    (wd / f"{tag}.errors.log").write_text(log, encoding="utf-8")


def ids_for(trips):
    return {"planned": [t.trip_id for t in trips], "cohort": [t.trip_id for t in trips if t.cohort],
            "trips": {t.trip_id: t for t in trips}}


TRIPS = [trip("b.fwd.1", "fwd", 25200), trip("b.rev.1", "rev", 25200),
         trip("b.fwd.0", "fwd", 24000, cohort=False)]


def test_complete_run_is_valid(tmp_path):
    _write(tmp_path, "t", [("b.fwd.1", 25200, 0, 27900, 2700), ("b.rev.1", 25200, 0, 28200, 3000),
                           ("b.fwd.0", 24000, 0, 26700, 2700)])
    p = parse_run(tmp_path, "t", ids_for(TRIPS))
    assert p["valid"] and p["id_sets"]["arrived"] == 3
    o = run_outcome(p, {"fwd": [], "rev": []}, pd.DataFrame(columns=["dir", "board_stop", "alight_stop", "demand"]))
    assert o["primary_min"] == pytest.approx(0.5 * 45 + 0.5 * 50)


def test_unfinished_cohort_bus_invalidates(tmp_path):
    _write(tmp_path, "t", [("b.fwd.1", 25200, 0, -1, 15000), ("b.rev.1", 25200, 0, 28200, 3000),
                           ("b.fwd.0", 24000, 0, 26700, 2700)])
    p = parse_run(tmp_path, "t", ids_for(TRIPS))
    assert not p["valid"] and p["problems"][0]["code"] == "cohort_not_arrived"
    assert run_outcome(p, {}, pd.DataFrame())["primary_min"] is None     # no partial durations


def test_teleported_and_missing_cohort_bus(tmp_path):
    _write(tmp_path, "t", [("b.fwd.1", 25200, 0, 27900, 2700), ("b.fwd.0", 24000, 0, 26700, 2700)],
           log="Warning: Teleporting vehicle 'b.fwd.1'; waited too long (jam)\n"
               "Warning: Teleporting vehicle 'car_17'; waited too long\n")
    p = parse_run(tmp_path, "t", ids_for(TRIPS))
    codes = {x["code"] for x in p["problems"]}
    assert {"cohort_teleported", "cohort_missing_from_output"} <= codes
    assert p["background_teleports"] == 1


def test_one_direction_flow_and_mixed_fleet(tmp_path):
    od = pd.DataFrame({"board_stop": ["A"], "alight_stop": ["C"], "hour": [7], "demand": [90.0]})
    act = stop_activity(od, ["A", "B", "C"])
    assert ("am_peak", "rev") not in act and act[("am_peak", "fwd")] == {"A": 90.0, "C": 90.0}
    trips = [trip("x.fwd.0", "fwd", 25200, vtype="bus18"), trip("x.fwd.1", "fwd", 25800),
             trip("x.rev.0", "rev", 25200)]
    bst = {"fwd": [("A", "bs_f_A"), ("B", "bs_f_B"), ("C", "bs_f_C")],
           "rev": [("C", "bs_r_C"), ("B", "bs_r_B"), ("A", "bs_r_A")]}
    ids = write_bus_routes(trips, act, {"fwd": ["e1"], "rev": ["r1"]}, bst,
                           {"dwell_base_s": 5, "dwell_per_pax_s": 1.5}, 0.3, 1, tmp_path / "b.rou.xml")
    xml = (tmp_path / "b.rou.xml").read_text()
    assert 'id="x.fwd.0" type="bus18" route="fwd"' in xml and 'id="x.fwd.1" type="bus12"' in xml
    assert 'route="rev"' in xml and len(ids["cohort"]) == 3


def test_missing_background_tool_is_error(monkeypatch, tmp_path):
    monkeypatch.setattr(background, "sumo_home", lambda: None)
    with pytest.raises(background.BackgroundError):
        background.generate_background(tmp_path / "n.net.xml", tmp_path, 1, 0, 10, 4, strict=True)
    assert background.generate_background(tmp_path / "n.net.xml", tmp_path, 1, 0, 10, 4) is None


def test_welch_interval_and_invalid_series():
    rng = np.random.default_rng(0)
    b, c = rng.normal(50, 1, 30), rng.normal(49, 2, 30)
    w = welch(b, c)
    from scipy import stats
    t = stats.ttest_ind(b, c, equal_var=False)
    assert w["p"] == pytest.approx(t.pvalue) and w["ci95"][0] < w["delta_min"] < w["ci95"][1]
    runs = [{"condition": "baseline", "seed": i, "valid": True, "primary_min": float(x),
             "mean_duration_fwd_min": float(x), "mean_duration_rev_min": float(x)} for i, x in enumerate(b)]
    runs += [{"condition": "candidate", "seed": 100 + i, "valid": True, "primary_min": float(x),
              "mean_duration_fwd_min": float(x), "mean_duration_rev_min": float(x)} for i, x in enumerate(c)]
    ok = analyse(runs, series="confirmatory", protocol_passed=True, evidence_kind="confirmatory")
    assert ok["completion_status"] == "complete" and ok["effect_status"] in ("improved", "inconclusive", "worsened")
    runs[0]["valid"] = False
    bad = analyse(runs, series="confirmatory", protocol_passed=True, evidence_kind="confirmatory")
    assert bad["effect_status"] == "not_tested" and bad["completion_status"] == "incomplete"
