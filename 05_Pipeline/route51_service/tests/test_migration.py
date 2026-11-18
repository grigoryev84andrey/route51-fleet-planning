"""Migration checks for schema 2 (plan S07)."""
import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import runctx  # noqa: E402
from layer1.reference import load_reference  # noqa: E402
from layer3 import provenance  # noqa: E402
from layer3.evaluate import paired_compare  # noqa: E402
from layer3.operations import canonical_stop_ids, default_params, evaluate_plan, precompute_demand  # noqa: E402
from layer3.optimize import Candidate, baseline_plan, rank_candidates  # noqa: E402
from layer5.decision import build_record  # noqa: E402

FINAL = ROOT.parents[1]
CFG = runctx.load_config(ROOT / "service_config.yaml")
FUSED = pd.read_csv(FINAL / "06_Results/tables/fused_demand_matrix.csv")


@pytest.fixture(scope="module")
def model():
    ref = load_reference(CFG)
    ids = canonical_stop_ids(ref)
    p = default_params(CFG, stop_ll=ref.stops, stop_ids=ids)
    f = FUSED.copy()
    f["demand"] *= CFG["operations"]["demand_scale"]
    return ids, p, precompute_demand(f, ids), f


def test_legacy_quantities_match_s04_control(model):
    """wait, cost and the historical overcrowding proxy of the baseline equal the
    unmodified-code control run (06_Results/runs/ctl_legacy_20260922_b6348aa4)."""
    ctl_path = FINAL / "06_Results/runs/ctl_legacy_20260922_b6348aa4/output/baseline_kpis.csv"
    if not ctl_path.exists():
        pytest.skip("legacy control run is project history and is not shipped in the deposit")
    ids, p, s, _ = model
    bh, bf = baseline_plan(CFG)
    m = evaluate_plan(bh, bf, s, p)
    ctl = pd.read_csv(ctl_path).iloc[0]
    assert m["wait"] == pytest.approx(ctl["wait"], abs=0.01)
    assert m["cost"] == pytest.approx(ctl["cost"], abs=0.01)
    assert m["legacy_overcrowd_proxy"] == pytest.approx(ctl["overcrowd"], abs=0.01)
    assert m["wait"] + 10 * m["legacy_overcrowd_proxy"] == pytest.approx(ctl["gen_cost"], abs=0.1)


def test_no_overcrowd_key_left(model):
    ids, p, s, _ = model
    bh, bf = baseline_plan(CFG)
    assert "overcrowd" not in evaluate_plan(bh, bf, s, p)


def test_crowding_change_changes_input_fingerprint():
    cfg2 = copy.deepcopy(CFG)
    cfg2["crowding"]["threshold"] = 0.9
    cfg2["operations"]["comfort_load_factor"] = 0.9
    assert provenance.input_fp(FUSED, CFG) != provenance.input_fp(FUSED, cfg2)


def test_selection_rule_ties():
    a = Candidate("43:5", np.ones(4), np.zeros(4), 10.0, 5.0, 10.0)
    b = Candidate("42:9", np.ones(4), np.zeros(4), 15.0, 5.0, 5.0)    # same gen_cost, same cost
    c = Candidate("44:0", np.ones(4), np.zeros(4), 5.0, 4.0, 15.0)    # same gen_cost, cheaper
    d = Candidate("42:1", np.ones(4), np.zeros(4), 1.0, 9.0, 1.0)     # lowest gen_cost
    assert [x.cid for x in rank_candidates([a, b, c, d])] == ["42:1", "44:0", "42:9", "43:5"]


def test_paired_compare_kpi_is_new_metric(model):
    ids, p, s, f = model
    bh, bf = baseline_plan(CFG)
    r = paired_compare(f, ids, p, bh, bf, bh, bf, n=3)
    assert r["mean_improvement"] == 0.0 and r["kpi"].startswith("gen_cost")
    assert r["evidence_kind"] == "exploratory"


def test_old_schema_is_refused():
    with pytest.raises(ValueError):
        build_record({"schema_version": 1})
