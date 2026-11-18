"""Common set-up and selection for layer-3 scripts (central run and sweeps).

Every script that re-optimises under changed conditions uses this module, so the
selection rule (protocol route51_v2: wait + crowd_min, ties by cost and stable
id, first candidate feasible after dispatch) and the metric definitions are the
same everywhere.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from layer1.reference import load_reference

from .dispatch import build_dispatch, dispatch_metrics, feasibility, select_executable
from .operations import canonical_stop_ids, default_params, evaluate_plan, precompute_demand
from .optimize import baseline_plan, candidates_from, rank_candidates, run_nsga2


@dataclass
class Study:
    cfg: dict
    stop_ids: list
    stop_ll: dict
    params: dict
    fused_raw: pd.DataFrame
    scale: float
    summary: dict
    bh: np.ndarray
    bf: np.ndarray
    base_rel: dict
    base_trips: list
    base_disp: dict
    budget: float
    delta: float
    pop: int
    gen: int
    extra: dict = field(default_factory=dict)

    @property
    def base_gen_cost(self) -> float:
        """Baseline wait + crowd_min on its dispatch (the comparable basis)."""
        return self.base_disp["gen_cost"]

    def optimise(self, *, seeds=(42, 43, 44), delta=None, budget=None, pop=None, gen=None,
                 history=False) -> dict:
        d = self.delta if delta is None else delta
        b = self.budget if budget is None else budget
        return {s: run_nsga2(self.summary, self.params, b, d, pop=pop or self.pop,
                             gen=gen or self.gen, seed=s, save_history=history)
                for s in seeds}

    def select(self, runs: dict, *, delta=None, budget=None, label="candidate") -> dict:
        d = self.delta if delta is None else delta
        b = self.budget if budget is None else budget
        pool = [c for s, r in runs.items() for c in candidates_from(r, s)]
        ranked = rank_candidates(pool)
        cand, trips, disp, log = select_executable(ranked, self.summary, self.params, b, d,
                                                   label=label)
        per_seed = {}
        for s, r in runs.items():
            rs = rank_candidates(candidates_from(r, s))
            per_seed[s] = rs[0] if rs else None
        return {"candidate": cand, "trips": trips, "dispatch": disp, "log": log,
                "relaxed": (evaluate_plan(cand.headways, cand.shares, self.summary, self.params)
                            if cand is not None else None),
                "pool_size": len(pool), "per_seed_best": per_seed}


def setup(cfg: dict, fused_raw: pd.DataFrame, *, demand_scale: float | None = None,
          cfg_overrides: dict | None = None) -> Study:
    """Build the model for `cfg` (optionally with nested overrides) and the baseline."""
    cfg = copy.deepcopy(cfg)
    for block, kv in (cfg_overrides or {}).items():
        cfg.setdefault(block, {}).update(kv)
    ref = load_reference(cfg)
    stop_ids = canonical_stop_ids(ref)
    params = default_params(cfg, stop_ll=ref.stops, stop_ids=stop_ids)
    op = cfg["operations"]
    scale = float(op["demand_scale"] if demand_scale is None else demand_scale)
    fused = fused_raw.copy()
    fused["demand"] = fused["demand"] * scale
    summary = precompute_demand(fused, stop_ids)
    bh, bf = baseline_plan(cfg)
    base_rel = evaluate_plan(bh, bf, summary, params)
    base_trips = build_dispatch(bh, bf, params, label="baseline")
    base_disp = dispatch_metrics(base_trips, summary, params, shares_desired=bf)
    ga = op.get("nsga2", {})
    return Study(cfg=cfg, stop_ids=stop_ids, stop_ll=ref.stops, params=params,
                 fused_raw=fused_raw, scale=scale, summary=summary, bh=bh, bf=bf,
                 base_rel=base_rel, base_trips=base_trips, base_disp=base_disp,
                 budget=base_rel["cost"] * op["tco_factor"], delta=op["equity_delta_default"],
                 pop=int(ga.get("pop", 120)), gen=int(ga.get("gen", 300)))


def row_for(study: Study, sel: dict, **tags) -> dict:
    """One tidy result row: selected plan on its dispatch vs the baseline dispatch."""
    c, m = sel["candidate"], sel["dispatch"]
    base = study.base_disp
    if c is None:
        return {**tags, "feasible": False, "reason": "no_feasible_executable_candidate",
                "examined": len(sel["log"])}
    seeds_gc = [v.gen_cost for v in sel["per_seed_best"].values() if v is not None]
    return {**tags, "feasible": True, "cid": c.cid,
            "wait": m["wait"], "crowd_min": m["crowd_min"], "gen_cost": m["gen_cost"],
            "cost": m["cost"], "fleet_needed": m["fleet_needed"],
            "headway_spread": m["headway_spread"], "max_phi": m["max_phi"],
            "baseline_gen_cost": base["gen_cost"], "baseline_wait": base["wait"],
            "baseline_crowd_min": base["crowd_min"], "baseline_cost": base["cost"],
            "gen_cost_change_pct": 100 * (m["gen_cost"] - base["gen_cost"]) / base["gen_cost"],
            "wait_change_pct": 100 * (m["wait"] - base["wait"]) / base["wait"],
            "crowd_min_change_pct": (100 * (m["crowd_min"] - base["crowd_min"]) / base["crowd_min"]
                                     if base["crowd_min"] > 0 else None),
            "cost_change_pct": 100 * (m["cost"] - base["cost"]) / base["cost"],
            "worst_headway_min": max(c.headways), "best_headway_min": min(c.headways),
            "seed_best_relaxed_gen_cost_min": min(seeds_gc) if seeds_gc else None,
            "seed_best_relaxed_gen_cost_max": max(seeds_gc) if seeds_gc else None,
            "examined": len(sel["log"]), "pool_size": sel["pool_size"]}


# --------------------------------------------------------------------------- #
# Process-pool helpers (picklable): the same optimisation, one seed per task
# --------------------------------------------------------------------------- #
def optimise_one_seed(task: tuple) -> list:
    """task = (cfg, fused_raw, demand_scale, cfg_overrides, seed) -> [Candidate]."""
    cfg, fused_raw, scale, overrides, seed = task
    from .optimize import candidates_from as _cands
    st = setup(cfg, fused_raw, demand_scale=scale, cfg_overrides=overrides)
    res = run_nsga2(st.summary, st.params, st.budget, st.delta, pop=st.pop, gen=st.gen, seed=seed)
    return _cands(res, seed)


def select_from_candidates(st: Study, cands: list, label: str = "candidate") -> dict:
    """Protocol selection over an already-computed candidate pool."""
    ranked = rank_candidates(cands)
    cand, trips, disp, log = select_executable(ranked, st.summary, st.params, st.budget, st.delta,
                                               label=label)
    per_seed = {}
    for c in ranked:
        s = int(c.cid.split(":")[0])
        per_seed.setdefault(s, c)
    return {"candidate": cand, "trips": trips, "dispatch": disp, "log": log, "ranked": ranked,
            "relaxed": (evaluate_plan(cand.headways, cand.shares, st.summary, st.params)
                        if cand is not None else None),
            "pool_size": len(cands), "per_seed_best": per_seed}
