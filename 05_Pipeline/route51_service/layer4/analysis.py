"""Layer 4 — registered analysis of a simulation series (protocol route51_v2 v1.0).

Observation unit: one value per run. Primary outcome: 0.5 * mean full-trip bus
duration (forward) + 0.5 * mean (reverse) over the 07:00-10:00 departure cohort.
Primary test: two-sided Welch t-test, alpha 0.05, delta = baseline - candidate,
95% Welch-Satterthwaite interval. Everything else is exploratory.

A series in which any run is invalid yields no confirmatory result
(effect_status = not_tested); the numbers of the valid runs are still reported
as diagnostics, never as the registered outcome.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import stats

import result_contracts as rc

ANALYSIS_VERSION = "welch_primary_v1"


def welch(base, cand, alpha=0.05) -> dict:
    b, c = np.asarray(base, float), np.asarray(cand, float)
    nb, nc = b.size, c.size
    if nb < 2 or nc < 2:
        return {"defined": False, "reason": "fewer_than_two_runs"}
    vb, vc = b.var(ddof=1), c.var(ddof=1)
    delta = float(b.mean() - c.mean())
    se2 = vb / nb + vc / nc
    if se2 == 0:
        return {"defined": False, "reason": "zero_variance", "delta_min": delta}
    se = math.sqrt(se2)
    df = se2 ** 2 / ((vb / nb) ** 2 / (nb - 1) + (vc / nc) ** 2 / (nc - 1))
    tcrit = float(stats.t.ppf(1 - alpha / 2, df))
    t = stats.ttest_ind(b, c, equal_var=False)
    return {"defined": True, "delta_min": delta, "se": se, "df": float(df),
            "t": float(t.statistic), "p": float(t.pvalue),
            "ci95": [delta - tcrit * se, delta + tcrit * se],
            "baseline_mean": float(b.mean()), "candidate_mean": float(c.mean()),
            "baseline_sd": float(math.sqrt(vb)), "candidate_sd": float(math.sqrt(vc)),
            "n_baseline": int(nb), "n_candidate": int(nc)}


def cohens_d(base, cand):
    b, c = np.asarray(base, float), np.asarray(cand, float)
    s = math.sqrt((b.var(ddof=1) + c.var(ddof=1)) / 2.0)
    return float((b.mean() - c.mean()) / s) if s > 0 else None


def analyse(runs: list[dict], *, series: str, protocol_passed: bool,
            evidence_kind: str) -> dict:
    """`runs`: dicts with condition ('baseline'|'candidate'), seed, valid, primary_min, ..."""
    base = [r for r in runs if r["condition"] == "baseline"]
    cand = [r for r in runs if r["condition"] == "candidate"]
    invalid = [{"condition": r["condition"], "seed": r["seed"], "problems": r.get("problems")}
               for r in runs if not r["valid"]]
    all_valid = not invalid and len(base) > 0 and len(cand) > 0
    B = [r["primary_min"] for r in base if r["valid"] and r["primary_min"] is not None]
    C = [r["primary_min"] for r in cand if r["valid"] and r["primary_min"] is not None]
    prim = welch(B, C)

    passed = protocol_passed and all_valid and prim.get("defined", False)
    effect = rc.classify_effect(*(prim["ci95"] if prim.get("defined") else (None, None)),
                                protocol_passed=passed)
    out = {
        "analysis_version": ANALYSIS_VERSION,
        "series": series,
        "evidence_kind": evidence_kind,
        "completion_status": "complete" if all_valid else "incomplete",
        "protocol_status": "passed" if passed else ("failed" if invalid else "not_checked"),
        "effect_status": effect,
        "invalid_runs": invalid,
        "primary": {**prim, "outcome": "0.5*mean_fwd + 0.5*mean_rev full-trip bus duration, min",
                    "delta_definition": "baseline - candidate"},
    }
    if len(B) >= 2 and len(C) >= 2:
        mw = stats.mannwhitneyu(B, C, alternative="two-sided")
        out["secondary_exploratory"] = {
            "cohens_d": cohens_d(B, C),
            "cohens_d_definition": "delta / sqrt((s_b^2 + s_c^2) / 2)",
            "mann_whitney": {"U": float(mw.statistic), "p": float(mw.pvalue)},
            "direction_fwd": welch([r["mean_duration_fwd_min"] for r in base if r["valid"]],
                                   [r["mean_duration_fwd_min"] for r in cand if r["valid"]]),
            "direction_rev": welch([r["mean_duration_rev_min"] for r in base if r["valid"]],
                                   [r["mean_duration_rev_min"] for r in cand if r["valid"]]),
            "terminal_headway_cv": {
                c: {d: _mean([r.get(f"terminal_headway_cv_{d}") for r in grp if r["valid"]])
                    for d in ("fwd", "rev")}
                for c, grp in (("baseline", base), ("candidate", cand))},
            "depart_delay_mean_s": {c: _mean([r.get("mean_depart_delay_s") for r in grp if r["valid"]])
                                    for c, grp in (("baseline", base), ("candidate", cand))},
            "pax_ivt_am_min": {c: _mean([r.get("pax_ivt_am_min") for r in grp if r["valid"]])
                               for c, grp in (("baseline", base), ("candidate", cand))},
        }
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return float(np.mean(xs)) if xs else None
