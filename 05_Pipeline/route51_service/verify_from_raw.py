"""Independent recomputation of a series' summary from its raw per-bus rows (plan S14.8).

Reads buses.csv of a run_replications series and recomputes, without importing
layer4.analysis: the per-run primary value (0.5 * forward mean + 0.5 * reverse
mean of cohort-bus durations), the Welch test and its 95% interval, and compares
them with simulation_analysis.json.

Usage: python verify_from_raw.py --output-dir DIR --input-dir <series run dir>
Outputs: verification.json
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

import runctx  # noqa: E402


def main() -> None:
    ctx = runctx.start(__file__, inputs=["buses.csv", "runs.csv", "simulation_analysis.json"])
    b = pd.read_csv(ctx.input("buses.csv"))
    runs = pd.read_csv(ctx.input("runs.csv"))
    a = json.loads(ctx.input("simulation_analysis.json").read_text(encoding="utf-8"))
    b["dur"] = b["duration_s"] / 60.0
    per_run = (b.groupby(["condition", "seed", "direction"])["dur"].mean().unstack("direction"))
    per_run["primary"] = 0.5 * per_run["fwd"] + 0.5 * per_run["rev"]
    per_run = per_run.reset_index()
    # compare per-run values with runs.csv
    m = per_run.merge(runs[["condition", "seed", "primary_min", "valid"]], on=["condition", "seed"])
    max_run_diff = float((m["primary"] - m["primary_min"]).abs().max())
    B = per_run[per_run.condition == "baseline"]["primary"].to_numpy()
    C = per_run[per_run.condition == "candidate"]["primary"].to_numpy()
    out = {"runs_checked": int(len(m)), "all_runs_valid": bool(runs["valid"].all()),
           "max_abs_diff_per_run_primary": max_run_diff}
    if len(B) >= 2 and len(C) >= 2:
        vb, vc = B.var(ddof=1), C.var(ddof=1)
        se = math.sqrt(vb / len(B) + vc / len(C))
        df = (vb / len(B) + vc / len(C)) ** 2 / ((vb / len(B)) ** 2 / (len(B) - 1) + (vc / len(C)) ** 2 / (len(C) - 1))
        d = B.mean() - C.mean()
        t = stats.ttest_ind(B, C, equal_var=False)
        q = stats.t.ppf(0.975, df)
        mine = {"delta_min": d, "ci95": [d - q * se, d + q * se], "p": float(t.pvalue), "df": df,
                "baseline_mean": B.mean(), "candidate_mean": C.mean()}
        pr = a["primary"]
        diffs = {k: abs(float(np.asarray(mine[k]).ravel()[0] if k != "ci95" else max(
                    abs(mine["ci95"][0] - pr["ci95"][0]), abs(mine["ci95"][1] - pr["ci95"][1]))) -
                     (0 if k == "ci95" else float(pr[k])))
                 for k in ("delta_min", "p", "df", "baseline_mean", "candidate_mean", "ci95")}
        out.update({"recomputed": {k: (v if not isinstance(v, np.ndarray) else v.tolist()) for k, v in mine.items()},
                    "max_abs_diff_vs_analysis": diffs, "agrees_1e-9": all(v < 1e-9 for v in diffs.values())})
    (ctx.out / "verification.json").write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(json.dumps(out, indent=2, default=float))


if __name__ == "__main__":
    main()
