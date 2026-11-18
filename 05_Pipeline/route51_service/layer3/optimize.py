"""Layer 3 — NSGA-II optimizer (pymoo).

Decision vector: per-period headway (minutes) + per-period capacity mix (share of
18 m buses). Three objectives minimised — wait, cost index, crowding disutility
(`crowd_min`, pax·min/day) — subject to:
  - cost cap       : operator cost index <= budget (baseline cost * tco_factor)
  - headway spread : (max - min) / mean headway across periods <= delta
  - fleet          : peak concurrent vehicles <= fleet

Selection rule (protocol route51_v2): minimise `wait + crowd_min`; ties are broken
by lower cost, then by the stable candidate id `<seed>:<index>`. There is no
hidden weight on crowding: both terms are passenger-minutes.

NOTE — the architecture diagram also shows a "DRL benchmark"; it is intentionally
NOT built. NSGA-II is the sole optimizer.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.core.problem import ElementwiseProblem
from pymoo.optimize import minimize

from .operations import PERIOD_NAMES, evaluate_plan

OBJECTIVES = ("wait", "cost", "crowd_min")


class BusOptProblem(ElementwiseProblem):
    def __init__(self, summary, params, budget, delta):
        P = len(PERIOD_NAMES)
        xl = np.array([params["h_min"]] * P + [0.0] * P)
        xu = np.array([params["h_max"]] * P + [1.0] * P)
        super().__init__(n_var=2 * P, n_obj=3, n_ieq_constr=3, xl=xl, xu=xu)
        self.summary, self.params, self.budget, self.delta = summary, params, budget, delta

    def _evaluate(self, x, out, *args, **kwargs):
        P = len(PERIOD_NAMES)
        m = evaluate_plan(x[:P], x[P:], self.summary, self.params)
        out["F"] = [m[k] for k in OBJECTIVES]
        out["G"] = [m["cost"] - self.budget,
                    m["headway_spread"] - self.delta,
                    m["max_concurrent"] - self.params["fleet"]]


def run_nsga2(summary, params, budget, delta, pop=60, gen=60, seed=42,
              save_history=False):
    """Run NSGA-II. save_history=True retains the per-generation population so the
    hypervolume convergence curve can be reconstructed."""
    problem = BusOptProblem(summary, params, budget, delta)
    # Operators set explicitly (thesis §2.5.1); they equal the pymoo 0.6 defaults.
    algo = NSGA2(pop_size=pop,
                 crossover=SBX(prob=0.9, eta=15),
                 mutation=PM(eta=20))
    return minimize(problem, algo, ("n_gen", gen), seed=seed,
                    save_history=save_history, verbose=False)


def hv_curve(res, ref=None):
    """Hypervolume after each generation, from a save_history=True result.

    The reference point, if not given, is 10% worse than the worst value each
    objective attains anywhere in this history. Pass one shared `ref` to compare
    several runs on a common scale.
    """
    from pymoo.indicators.hv import Hypervolume

    fronts = [np.atleast_2d(h.opt.get("F")) for h in res.history]
    if ref is None:
        ref = np.vstack(fronts).max(axis=0) * 1.1
    hv = Hypervolume(ref_point=np.asarray(ref, float))
    return np.array([hv(F) for F in fronts]), np.asarray(ref, float)


def history_fronts(res) -> list[np.ndarray]:
    return [np.atleast_2d(h.opt.get("F")) for h in res.history]


def shared_reference(results) -> np.ndarray:
    """Common HV reference point for several runs: 10% beyond the worst value seen."""
    allF = np.vstack([F for r in results for F in history_fronts(r)])
    return allF.max(axis=0) * 1.1


def hv_stabilised(hv: np.ndarray, tail: float = 0.2, tol: float = 0.01) -> dict:
    """Protocol criterion: relative HV gain over the last `tail` share of generations."""
    hv = np.asarray(hv, float)
    if hv.size == 0 or hv[-1] <= 0:
        return {"defined": False, "stabilised": None, "rel_gain": None,
                "reason": "zero_hypervolume"}
    k = max(1, int(round(len(hv) * (1 - tail))) - 1)
    start = hv[k]
    gain = (hv[-1] - start) / hv[-1]
    return {"defined": True, "stabilised": bool(gain <= tol), "rel_gain": float(gain),
            "from_generation": k + 1, "reason": None}


def baseline_plan(cfg):
    bh = cfg["operations"]["baseline_headways"]
    headways = np.array([bh[p] for p in PERIOD_NAMES], float)
    capfracs = np.zeros(len(PERIOD_NAMES))         # current ops: all 12 m
    return headways, capfracs


# --------------------------------------------------------------------------- #
# Candidates and the selection rule
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    cid: str                 # "<seed>:<index>"
    headways: np.ndarray
    shares: np.ndarray
    wait: float
    cost: float
    crowd_min: float

    @property
    def gen_cost(self) -> float:
        return self.wait + self.crowd_min

    def sort_key(self):
        seed, idx = self.cid.split(":")
        return (self.gen_cost, self.cost, int(seed), int(idx))


def candidates_from(res, seed: int) -> list[Candidate]:
    if res.F is None:
        return []
    F, X = np.atleast_2d(res.F), np.atleast_2d(res.X)
    P = len(PERIOD_NAMES)
    return [Candidate(f"{seed}:{i}", X[i, :P].copy(), X[i, P:].copy(),
                      float(F[i, 0]), float(F[i, 1]), float(F[i, 2]))
            for i in range(len(F))]


def rank_candidates(cands: list[Candidate]) -> list[Candidate]:
    """Order by the protocol rule: wait + crowd_min, then cost, then stable id."""
    return sorted(cands, key=lambda c: c.sort_key())


def recommend(res, seed: int = 0):
    """Best candidate of one run by the protocol rule: (headways, shares, F row)."""
    ranked = rank_candidates(candidates_from(res, seed))
    if not ranked:
        return None
    c = ranked[0]
    return c.headways, c.shares, np.array([c.wait, c.cost, c.crowd_min])


def equity_frontier(summary, params, budget, deltas, pop=60, gen=60, seed=42):
    """Best achievable `wait + crowd_min` for each headway-spread cap delta."""
    rows = []
    for d in deltas:
        res = run_nsga2(summary, params, budget, d, pop=pop, gen=gen, seed=seed)
        ranked = rank_candidates(candidates_from(res, seed))
        if not ranked:
            rows.append({"delta": d, "feasible": False})
            continue
        c = ranked[0]
        rows.append({"delta": d, "feasible": True,
                     "gen_cost": c.gen_cost, "wait": c.wait, "cost": c.cost,
                     "crowd_min": c.crowd_min, "n_pareto": len(ranked)})
    return rows
