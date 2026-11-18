"""Layer 3 — model-level comparison of two plans under resampled demand.

Draws n paired stochastic demand realisations (Poisson resampling of each OD
flow), evaluates the baseline and a candidate plan under each draw, and runs a
paired t-test on the per-draw difference. This is an exploratory check of the
operations model's sensitivity to demand noise, not a simulation experiment and
not the registered test of H1_bus_v2.

KPI (schema 2): gen_cost = wait + crowd_min, both in pax·min/day. The historical
KPI (wait + 10 * overcrowding proxy) is available only with `legacy=True`, for
comparison with the pre-2026-09 control.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from .operations import evaluate_plan, precompute_demand

LEGACY_CROWD_PENALTY = 10.0   # historical weight of legacy_overcrowd_proxy


def _kpi(headways, capfracs, summary, params, legacy: bool):
    m = evaluate_plan(headways, capfracs, summary, params)
    if legacy:
        return m["wait"] + LEGACY_CROWD_PENALTY * m["legacy_overcrowd_proxy"]
    return m["gen_cost"]


def paired_compare(fused: pd.DataFrame, stop_ids, params,
                   base_h, base_f, opt_h, opt_f, n=60, seed=7, legacy: bool = False):
    rng = np.random.default_rng(seed)
    base_kpi, opt_kpi = [], []
    dem = fused["demand"].to_numpy()
    for _ in range(n):
        draw = fused.copy()
        draw["demand"] = rng.poisson(np.maximum(dem, 0)).astype(float)
        summ = precompute_demand(draw, stop_ids)
        base_kpi.append(_kpi(base_h, base_f, summ, params, legacy))
        opt_kpi.append(_kpi(opt_h, opt_f, summ, params, legacy))

    base_kpi, opt_kpi = np.array(base_kpi), np.array(opt_kpi)
    diff = base_kpi - opt_kpi                                  # >0 means candidate is better
    t, pval = stats.ttest_rel(base_kpi, opt_kpi)
    sd = diff.std(ddof=1)
    return {
        "kpi": "wait + 10*legacy_overcrowd_proxy (legacy)" if legacy else "gen_cost = wait + crowd_min",
        "unit": "mixed (legacy)" if legacy else "pax_min_per_day",
        "evidence_kind": "exploratory",
        "n": n,
        "seed": seed,
        "baseline_kpi_mean": float(base_kpi.mean()),
        "optimized_kpi_mean": float(opt_kpi.mean()),
        "mean_improvement": float(diff.mean()),
        "improvement_pct": 100 * float(diff.mean()) / float(base_kpi.mean()),
        "t_stat": float(t),
        "p_value": float(pval),
        "cohens_d_paired": float(diff.mean() / sd) if sd > 0 else None,
    }
