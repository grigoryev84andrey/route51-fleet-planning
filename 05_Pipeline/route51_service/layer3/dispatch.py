"""Layer 3 — executable dispatch: from a continuous plan to individual trips.

The optimiser works on a relaxation (continuous headway, continuous share of
18 m buses). An operator runs whole trips with whole vehicles. This module
builds those trips and re-evaluates the plan on them; the same trip list feeds
the simulation (layer 4), the resource count and the export.

Rules (protocol route51_v2 v1.0, section `dispatch`):

  * trips are built per continuous period block and per direction; off_peak is
    two blocks (05-07, 19-23) and no trip or wait is created across the gap;
  * departures at block_start + k*h, rounded to whole seconds, while < block_end,
    identical for both termini;
  * in a block with N trips and desired 18 m share f, n_large = floor(f*N + 0.5)
    trips are 18 m, spread by the Bresenham rule (trip k is large iff
    floor((k+1)*n_large/N) - floor(k*n_large/N) = 1);
  * the measured cohort is the half-open interval [07:00, 10:00) of planned
    departure from the first stop of the direction.

Re-evaluation after dispatch (`dispatch_metrics`):

  * waiting — passengers arrive uniformly within each hour at that hour's
    boarding rate (by direction) and wait for the next departure; within a
    block the wait for a gap g at rate r is r * g^2 / 2, integrated piecewise
    over hour boundaries; the last gap of a block runs to the block end (the
    next block starts there, or service ends). This is a timetable-based
    estimate, not a simulated wait;
  * cost index — sum over trips of relative vehicle cost * route km;
  * crowding — per period and direction with the actual trip count n and the
    mean full capacity c of those trips (average-vehicle assumption kept);
  * constraints — cost cap, headway spread on the effective headways
    (block minutes / trips), normative fleet: the largest number of forward
    departures inside any window of one cycle. The last is a normative check,
    not a vehicle-block schedule, so operational feasibility stays unknown.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import numpy as np

from .operations import PERIOD_BLOCKS, PERIOD_NAMES, PERIODS, crowding_terms

COHORT_WINDOW_S = (7 * 3600, 10 * 3600)
DIRECTIONS = ("fwd", "rev")
DISPATCH_VERSION = "dispatch_v1"


@dataclass(frozen=True)
class Trip:
    trip_id: str
    period: str
    block: int
    direction: str
    k: int
    planned_dep_s: int
    vtype: str
    capacity: int
    rel_cost: float
    length_km: float
    cohort: bool

    def as_dict(self) -> dict:
        return asdict(self)


def large_pattern(N: int, n_large: int) -> list[bool]:
    return [(math.floor((k + 1) * n_large / N) - math.floor(k * n_large / N)) == 1
            for k in range(N)]


def n_large_for(N: int, f: float) -> int:
    return int(math.floor(f * N + 0.5))


def build_dispatch(headways, shares, params: dict, label: str = "plan") -> list[Trip]:
    """Trips of a continuous plan. `headways`/`shares` follow PERIOD_NAMES."""
    trips: list[Trip] = []
    for p_idx, per in enumerate(PERIOD_NAMES):
        h = float(headways[p_idx])
        f = float(shares[p_idx])
        if not (h > 0) or not math.isfinite(h):
            raise ValueError(f"headway of {per} must be a positive finite number, got {h}")
        if not (0.0 <= f <= 1.0):
            raise ValueError(f"18 m share of {per} must lie in [0, 1], got {f}")
        for b, (h0, h1) in enumerate(PERIOD_BLOCKS[per]):
            start, end = h0 * 3600, h1 * 3600
            deps = []
            k = 0
            while True:
                t = start + int(round(k * h * 60.0))
                if t >= end:
                    break
                deps.append(t)
                k += 1
            N = len(deps)
            pattern = large_pattern(N, n_large_for(N, f)) if N else []
            for d in DIRECTIONS:
                for k, (t, big) in enumerate(zip(deps, pattern)):
                    trips.append(Trip(
                        trip_id=f"{label}.{d}.{per}.b{b}.{k:03d}",
                        period=per, block=b, direction=d, k=k, planned_dep_s=t,
                        vtype="bus18" if big else "bus12",
                        capacity=int(params["cap_large"] if big else params["cap_small"]),
                        rel_cost=float(params["veh_cost_large"] if big else params["veh_cost_small"]),
                        length_km=float(params["route_km"]),
                        cohort=COHORT_WINDOW_S[0] <= t < COHORT_WINDOW_S[1]))
    ids = [t.trip_id for t in trips]
    if len(set(ids)) != len(ids):
        raise AssertionError("duplicate trip ids")
    return trips


# --------------------------------------------------------------------------- #
# Waiting on the actual timetable
# --------------------------------------------------------------------------- #
def _wait_block(deps_s: list[int], start_s: int, end_s: int, hourly: dict) -> float:
    """pax·min of waiting in one block and direction.

    Arrivals at rate r(hour) = boardings(hour)/60 per minute, uniform within the
    hour. Each arrival waits until the next departure; the last gap ends at the
    block end. Integrated exactly, piecewise over hour boundaries.
    """
    if end_s <= start_s:
        return 0.0
    # Arrivals before the first departure wait for it; after the last, until end_s.
    bounds = sorted(set([d for d in deps_s if start_s <= d < end_s] + [end_s]))
    total = 0.0
    prev = start_s
    for nxt in bounds:
        a, b = prev, nxt                       # arrivals in [a, b) wait until b
        t = a
        while t < b:
            hour = t // 3600
            seg_end = min(b, (hour + 1) * 3600)
            rate = hourly.get(int(hour), 0.0) / 3600.0         # pax per second
            # integral of (b - s) ds over [t, seg_end), in seconds^2 -> pax*s
            total += rate * ((b - t) ** 2 - (b - seg_end) ** 2) / 2.0
            t = seg_end
        prev = nxt
    return total / 60.0                          # pax*s -> pax*min


def dispatch_wait_by_period(trips: list[Trip], summary: dict) -> dict:
    hourly = summary["_hourly_boardings"]
    out = {}
    for per in PERIOD_NAMES:
        w = 0.0
        for b, (h0, h1) in enumerate(PERIOD_BLOCKS[per]):
            for d in DIRECTIONS:
                deps = sorted(t.planned_dep_s for t in trips
                              if t.period == per and t.block == b and t.direction == d)
                w += _wait_block(deps, h0 * 3600, h1 * 3600, hourly[d])
        out[per] = w
    return out


def dispatch_wait(trips: list[Trip], summary: dict) -> float:
    return sum(dispatch_wait_by_period(trips, summary).values())


# --------------------------------------------------------------------------- #
# Metrics and feasibility on the dispatch
# --------------------------------------------------------------------------- #
def fleet_needed(trips: list[Trip], cycle_min: float) -> int:
    """Largest number of forward departures inside any window of one cycle."""
    deps = sorted(t.planned_dep_s for t in trips if t.direction == "fwd")
    w = cycle_min * 60.0
    best, j = 0, 0
    for i in range(len(deps)):
        while deps[i] - deps[j] >= w:
            j += 1
        best = max(best, i - j + 1)
    return best


def dispatch_metrics(trips: list[Trip], summary: dict, params: dict,
                     shares_desired=None) -> dict:
    crowd = params["crowding"]
    seg = params["seg_min"]
    cost = sum(t.rel_cost * t.length_km for t in trips)
    by_period = {per: {"wait": 0.0, "crowd_min": 0.0, "ivt_min": 0.0,
                       "cost": sum(t.rel_cost * t.length_km for t in trips if t.period == per)}
                 for per in PERIOD_NAMES}
    ivt = crowd_min = crowded = 0.0
    max_phi, over = 0.0, 0
    per_rows = []
    eff_h = []
    for p_idx, per in enumerate(PERIOD_NAMES):
        block_min = sum((h1 - h0) * 60 for h0, h1 in PERIOD_BLOCKS[per])
        for d in DIRECTIONS:
            tp = [t for t in trips if t.period == per and t.direction == d]
            n = len(tp)
            c = float(np.mean([t.capacity for t in tp])) if n else 0.0
            ct = crowding_terms(summary[per][d]["load"], n, c, seg, crowd)
            ivt += ct["ivt_min"]
            crowd_min += ct["crowd_min"]
            crowded += ct["crowded_ivt_min"]
            by_period[per]["crowd_min"] += ct["crowd_min"]
            by_period[per]["ivt_min"] += ct["ivt_min"]
            max_phi = max(max_phi, ct["max_phi"])
            over += ct["over_capacity_segments"]
            n_large = sum(t.vtype == "bus18" for t in tp)
            per_rows.append({
                "period": per, "direction": d, "trips": n, "trips_18m": n_large,
                "share_18m_actual": n_large / n if n else None,
                "share_18m_desired": (float(shares_desired[p_idx])
                                      if shares_desired is not None else None),
                "mean_capacity": c, "max_phi": ct["max_phi"],
            })
        n_fwd = sum(1 for t in trips if t.period == per and t.direction == "fwd")
        eff_h.append(block_min / n_fwd if n_fwd else float("inf"))
    wait_p = dispatch_wait_by_period(trips, summary)
    for per in PERIOD_NAMES:
        by_period[per]["wait"] = wait_p[per]
        by_period[per]["gen_cost"] = wait_p[per] + by_period[per]["crowd_min"]
    wait = sum(wait_p.values())
    h = np.asarray(eff_h, float)
    spread = float((h.max() - h.min()) / h.mean()) if np.all(np.isfinite(h)) and h.mean() > 0 else float("inf")
    return {
        "wait": wait, "cost": cost, "crowd_min": crowd_min, "gen_cost": wait + crowd_min,
        "ivt_min": ivt,
        "crowded_ivt_share": crowded / ivt if ivt > 0 else None,
        "max_phi": max_phi, "over_capacity": over > 0, "over_capacity_segments": over,
        "effective_headways": dict(zip(PERIOD_NAMES, map(float, h))),
        "headway_spread": spread,
        "fleet_needed": fleet_needed(trips, params["cycle_min"]),
        "trips_total": len(trips),
        "cohort_trips": {d: sum(1 for t in trips if t.cohort and t.direction == d) for d in DIRECTIONS},
        "by_period_direction": per_rows,
        "by_period": by_period,
        "dispatch_version": DISPATCH_VERSION,
    }


def feasibility(m: dict, budget: float, delta: float, fleet: int) -> dict:
    checks = {
        "cost_within_budget": m["cost"] <= budget + 1e-9,
        "headway_spread_within_delta": m.get("headway_spread", 0.0) <= delta + 1e-12,
        "fleet_within_norm": m.get("fleet_needed", m.get("max_concurrent", 0)) <= fleet,
    }
    return {"feasible": all(checks.values()), "checks": checks,
            "operational_feasibility": "unknown"}


def select_executable(ranked, summary, params, budget, delta, label="candidate"):
    """Walk candidates in protocol order; return the first feasible after dispatch.

    `ranked` are optimize.Candidate objects already ordered by the selection rule.
    Re-ranking happens on the dispatch metrics of the candidates examined, so a
    candidate that improves after rounding cannot jump the queue unseen: the walk
    stops at the first feasible one in the relaxation order.
    Returns (candidate, trips, metrics, log) or (None, None, None, log).
    """
    log = []
    for c in ranked:
        trips = build_dispatch(c.headways, c.shares, params, label=label)
        m = dispatch_metrics(trips, summary, params, shares_desired=c.shares)
        fz = feasibility(m, budget, delta, params["fleet"])
        log.append({"cid": c.cid, "relaxed_gen_cost": c.gen_cost, "dispatch_gen_cost": m["gen_cost"],
                    "dispatch_cost": m["cost"], "feasible": fz["feasible"], **fz["checks"]})
        if fz["feasible"]:
            return c, trips, m, log
    return None, None, None, log


def trips_from_frame(df) -> list[Trip]:
    """Rebuild Trip objects from a dispatch CSV written by run_layer3.py."""
    out = []
    for r in df.to_dict(orient="records"):
        out.append(Trip(trip_id=str(r["trip_id"]), period=str(r["period"]), block=int(r["block"]),
                        direction=str(r["direction"]), k=int(r["k"]),
                        planned_dep_s=int(r["planned_dep_s"]), vtype=str(r["vtype"]),
                        capacity=int(r["capacity"]), rel_cost=float(r["rel_cost"]),
                        length_km=float(r["length_km"]),
                        cohort=bool(r["cohort"]) if not isinstance(r["cohort"], str)
                        else r["cohort"].strip().lower() == "true"))
    return out
