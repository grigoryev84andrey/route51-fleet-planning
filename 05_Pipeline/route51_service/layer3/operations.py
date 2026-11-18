"""Layer 3 — operations model.

Turns a service plan (per-period headway + capacity mix) into objectives, using
the fused stop-level OD to build the on-board load profile of every segment.

Objectives (all minimised):
  1. passenger wait      ~ boardings * headway / 2                      [pax·min/day]
  2. operator cost index ~ vehicle-trips * relative vehicle cost * route km   [index]
  3. crowding disutility crowd_min = sum over period, direction, segment of
                         (lambda(phi) - 1) * L * t                       [pax·min/day]

Crowding model (`crowding_model_version = ivt_multiplier_v1`). For period p,
direction d and segment s:

  L   = passengers carried over the segment during the period
  n   = trips in the period (relaxation: duration / headway)
  c   = mean full capacity of those trips (relaxation: 90 + 60 * share_18m)
  phi = L / (n * c)
  t   = segment running time, minutes

  lambda_none = 1
  lambda_step = 1 if phi <= theta else lambda_full
  lambda_ramp = 1 + (lambda_full - 1) * max(0, (phi - theta) / (1 - theta))
                (linear extension above phi = 1)

`gen_cost = wait + crowd_min` is the variable part of a generalised cost, not a
full journey time: in-vehicle running time is left out of the choice only
because it does not depend on the plan in this model. phi > 1 is flagged as
exceeding full capacity; the model does not move the excess passengers into
queues or deny boarding, so it does not claim they were physically carried.

The earlier criterion (passengers above comfortable capacity at the peak
segment, weighted by 10 minutes) survives only as `legacy_overcrowd_proxy`.

Simplifications (flagged): four time-of-day periods (off_peak = two separate
blocks 05-07 and 19-23); capacity is a continuous share of 18 m buses in the
relaxation; every trip of a period is treated as an average vehicle, so load
differences between individual trips are not modelled.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

CROWDING_MODEL_VERSION = "ivt_multiplier_v1"

PERIODS = {
    "am_peak":  [7, 8, 9],
    "midday":   [10, 11, 12, 13, 14, 15],
    "pm_peak":  [16, 17, 18],
    "off_peak": [5, 6, 19, 20, 21, 22],
}
PERIOD_NAMES = list(PERIODS)
# Continuous service blocks, [start_hour, end_hour). off_peak has two.
PERIOD_BLOCKS = {
    "am_peak": [(7, 10)],
    "midday": [(10, 16)],
    "pm_peak": [(16, 19)],
    "off_peak": [(5, 7), (19, 23)],
}

CROWDING_MODELS = ("ramp", "step", "none")


class DemandError(ValueError):
    """The demand table cannot be mapped onto the corridor without losing riders."""


# --------------------------------------------------------------------------- #
# Crowding
# --------------------------------------------------------------------------- #
def crowding_multiplier(phi, model: str, theta: float, lambda_full: float):
    """lambda(phi) for an array of load factors."""
    phi = np.asarray(phi, float)
    if model == "none":
        return np.ones_like(phi)
    if model == "step":
        return np.where(phi > theta, lambda_full, 1.0)
    if model == "ramp":
        return 1.0 + (lambda_full - 1.0) * np.maximum(0.0, (phi - theta) / (1.0 - theta))
    raise ValueError(f"unknown crowding model {model!r}")


def crowding_terms(load, n_trips: float, capacity: float, seg_min, crowd: dict) -> dict:
    """Crowding of one period and direction.

    load     — passengers per segment over the period (array, pax)
    n_trips  — trips in the period
    capacity — mean full capacity per trip (persons)
    seg_min  — segment running times (array, minutes)
    """
    load = np.asarray(load, float)
    seg_min = np.asarray(seg_min, float)
    offered = n_trips * capacity
    if offered <= 0:
        if load.sum() > 0:
            raise ValueError("demand on a period and direction with no capacity offered")
        phi = np.zeros_like(load)
    else:
        phi = load / offered
    lam = crowding_multiplier(phi, crowd["model"], crowd["theta"], crowd["lambda_full"])
    pax_min = load * seg_min
    return {
        "ivt_min": float(pax_min.sum()),
        "crowd_min": float(((lam - 1.0) * pax_min).sum()),
        "crowded_ivt_min": float(pax_min[phi > crowd["theta"]].sum()),
        "max_phi": float(phi.max()) if phi.size else 0.0,
        "over_capacity_segments": int((phi > 1.0).sum()),
        "phi": phi,
    }


# --------------------------------------------------------------------------- #
# Demand
# --------------------------------------------------------------------------- #
def precompute_demand(fused: pd.DataFrame, stop_ids: list[str]) -> dict:
    """Per period and direction: boardings, full segment-load profile, peak load.

    `stop_ids` is the canonical corridor sequence (forward direction). Segment k
    lies between stop_ids[k] and stop_ids[k+1] in both directions; the reverse
    profile is indexed by the same forward segment numbers (`load`) and is also
    given in travel order (`load_travel_order`).

    No rider is dropped silently: an hour outside the service periods, a stop
    outside the corridor, a negative or non-finite demand, or a trip that boards
    and alights at the same stop raises `DemandError`.
    """
    need = ["board_stop", "alight_stop", "hour", "demand"]
    missing = [c for c in need if c not in fused.columns]
    if missing:
        raise DemandError(f"demand table lacks columns {missing}")
    pos = {s: i for i, s in enumerate(stop_ids)}
    n = len(stop_ids)
    hour2period = {h: p for p, hs in PERIODS.items() for h in hs}

    f = fused[need].copy()
    dem = pd.to_numeric(f["demand"], errors="coerce")
    if dem.isna().any() or not np.all(np.isfinite(dem)):
        raise DemandError("non-numeric or non-finite demand values")
    if (dem < 0).any():
        raise DemandError(f"negative demand in {int((dem < 0).sum())} rows")
    f["demand"] = dem.astype(float)
    f["period"] = f["hour"].map(hour2period)
    if f["period"].isna().any():
        bad = sorted(set(f.loc[f["period"].isna(), "hour"].tolist()))
        raise DemandError(f"hours outside the service periods: {bad}")
    f["pi"] = f["board_stop"].map(pos)
    f["pj"] = f["alight_stop"].map(pos)
    if f["pi"].isna().any() or f["pj"].isna().any():
        bad = sorted(set(f.loc[f["pi"].isna(), "board_stop"]) | set(f.loc[f["pj"].isna(), "alight_stop"]))
        raise DemandError(f"stops outside the corridor: {bad}")
    f["pi"] = f["pi"].astype(int)
    f["pj"] = f["pj"].astype(int)
    same = f["pi"] == f["pj"]
    if same.any():
        raise DemandError(f"{int(same.sum())} rows board and alight at the same stop")
    f["dir"] = np.where(f["pi"] < f["pj"], "fwd", "rev")

    hourly = f.groupby(["dir", "hour"])["demand"].sum()
    summary: dict = {"_dur_min": {p: len(PERIODS[p]) * 60 for p in PERIOD_NAMES},
                     "_total_demand": float(f["demand"].sum()),
                     "_stop_ids": list(stop_ids),
                     "_hourly_boardings": {dr: {int(h): float(hourly.get((dr, h), 0.0))
                                                for h in hour2period}
                                           for dr in ("fwd", "rev")}}
    for per in PERIOD_NAMES:
        fp = f[f["period"] == per]
        summary[per] = {}
        for dr in ("fwd", "rev"):
            fpd = fp[fp["dir"] == dr]
            load = np.zeros(max(n - 1, 1))
            for pi, pj, d in zip(fpd["pi"], fpd["pj"], fpd["demand"]):
                lo, hi = (pi, pj) if pi < pj else (pj, pi)
                load[lo:hi] += d
            summary[per][dr] = {
                "boardings": float(fpd["demand"].sum()),
                "load": load,
                "load_travel_order": load if dr == "fwd" else load[::-1].copy(),
                "peak": float(load.max()) if load.size else 0.0,
            }
    kept = sum(summary[p][d]["boardings"] for p in PERIOD_NAMES for d in ("fwd", "rev"))
    if not math.isclose(kept, summary["_total_demand"], rel_tol=1e-12, abs_tol=1e-9):
        raise DemandError(f"demand lost in aggregation: {kept} of {summary['_total_demand']}")
    return summary


# --------------------------------------------------------------------------- #
# Segment times
# --------------------------------------------------------------------------- #
def _xy(la, lo, lat0, lon0):
    # Same planar projection as layer4/network.py, so the model and the SUMO
    # network use the same inter-stop lengths.
    return ((lo - lon0) * math.cos(math.radians(lat0)) * 111320.0,
            (la - lat0) * 110540.0)


def segment_lengths_m(stop_ids: list[str], stop_ll: dict) -> np.ndarray:
    lat0, lon0 = stop_ll[stop_ids[0]]
    xy = [_xy(*stop_ll[s], lat0, lon0) for s in stop_ids]
    return np.array([math.dist(a, b) for a, b in zip(xy[:-1], xy[1:])], float)


def segment_minutes(stop_ids: list[str], stop_ll: dict | None, one_way_min: float,
                    mode: str = "distance") -> np.ndarray:
    """Running time per segment, summing to the one-way norm (layover excluded)."""
    n_seg = len(stop_ids) - 1
    if n_seg < 1:
        raise ValueError("a corridor needs at least two stops")
    if mode == "uniform":
        return np.full(n_seg, one_way_min / n_seg)
    if mode != "distance":
        raise ValueError(f"unknown segment_times mode {mode!r}")
    if stop_ll is None:
        raise ValueError("distance-based segment times need stop coordinates")
    L = segment_lengths_m(stop_ids, stop_ll)
    if L.sum() <= 0:
        raise ValueError("zero total inter-stop distance; set segment_times: uniform explicitly")
    return one_way_min * L / L.sum()


# --------------------------------------------------------------------------- #
# Plan evaluation (relaxation)
# --------------------------------------------------------------------------- #
def evaluate_plan(headways, capfracs, summary: dict, p: dict) -> dict:
    """Objectives + constraint diagnostics for a continuous plan."""
    def cap(f): return p["cap_small"] + (p["cap_large"] - p["cap_small"]) * f
    def vcost(f): return p["veh_cost_small"] + (p["veh_cost_large"] - p["veh_cost_small"]) * f

    crowd = p["crowding"]
    seg = p["seg_min"]
    wait = cost = legacy = 0.0
    ivt = crowd_min = crowded_ivt = 0.0
    max_phi, over_cap = 0.0, 0
    lfs, max_concurrent = [], 0.0
    for k, per in enumerate(PERIOD_NAMES):
        h, f = float(headways[k]), float(capfracs[k])
        dur = summary["_dur_min"][per]
        trips_dir = dur / h                       # vehicle-trips per direction
        c = cap(f)
        for dr in ("fwd", "rev"):
            s = summary[per][dr]
            wait += s["boardings"] * h / 2.0
            cost += trips_dir * vcost(f) * p["route_km"]
            ct = crowding_terms(s["load"], trips_dir, c, seg, crowd)
            ivt += ct["ivt_min"]
            crowd_min += ct["crowd_min"]
            crowded_ivt += ct["crowded_ivt_min"]
            max_phi = max(max_phi, ct["max_phi"])
            over_cap += ct["over_capacity_segments"]
            legacy += max(0.0, s["peak"] - p["comfort"] * c * trips_dir)
            lfs.append(s["peak"] / (c * trips_dir) if trips_dir > 0 else 0.0)
        max_concurrent = max(max_concurrent, p["cycle_min"] / h)

    h_arr = np.asarray(headways, float)
    hw_spread = float((h_arr.max() - h_arr.min()) / h_arr.mean()) if h_arr.mean() > 0 else 0.0
    return {"wait": wait, "cost": cost, "crowd_min": crowd_min,
            "gen_cost": wait + crowd_min,
            "ivt_min": ivt,
            "crowded_ivt_share": crowded_ivt / ivt if ivt > 0 else None,
            "crowded_ivt_share_reason": None if ivt > 0 else "no_in_vehicle_time",
            "max_phi": max_phi,
            "over_capacity": over_cap > 0,
            "over_capacity_segments": over_cap,
            "legacy_overcrowd_proxy": legacy,
            "headway_spread": hw_spread,        # relative spread of headways across periods
            "lf_spread": (max(lfs) - min(lfs)) if lfs else 0.0,
            "max_concurrent": max_concurrent}


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #
def crowding_config(cfg) -> dict:
    """Validated crowding block; theta conflicts with comfort_load_factor are errors."""
    c = dict(cfg.get("crowding") or {})
    if not c:
        raise ValueError("service_config has no crowding block")
    model = c.get("model")
    theta = float(c.get("threshold"))
    lam = float(c.get("multiplier_at_full_capacity"))
    above = c.get("above_capacity", "linear_extension")
    seg = c.get("segment_times", "distance")
    if model not in CROWDING_MODELS:
        raise ValueError(f"crowding.model must be one of {CROWDING_MODELS}, got {model!r}")
    if not (0.0 < theta < 1.0):
        raise ValueError("crowding.threshold must lie in (0, 1)")
    if not (lam >= 1.0) or not math.isfinite(lam):
        raise ValueError("crowding.multiplier_at_full_capacity must be >= 1")
    if above != "linear_extension":
        raise ValueError("only above_capacity: linear_extension is implemented")
    if seg not in ("distance", "uniform"):
        raise ValueError("crowding.segment_times must be distance or uniform")
    comfort = cfg["operations"].get("comfort_load_factor")
    if comfort is not None and not math.isclose(float(comfort), theta):
        raise ValueError(f"operations.comfort_load_factor ({comfort}) conflicts with "
                         f"crowding.threshold ({theta}); keep one source of theta")
    return {"model": model, "theta": theta, "lambda_full": lam,
            "above_capacity": above, "segment_times": seg,
            "version": CROWDING_MODEL_VERSION}


def default_params(cfg, stop_ll: dict | None = None,
                   stop_ids: list[str] | None = None) -> dict:
    """Model parameters. Segment times need the stop catalogue; it is loaded from
    the configuration when not given."""
    o = cfg["operations"]
    fc = cfg["fleet_catalog"]
    crowd = crowding_config(cfg)
    if stop_ll is None or stop_ids is None:
        from layer1.reference import load_reference
        ref = load_reference(cfg)
        stop_ll = ref.stops if stop_ll is None else stop_ll
        stop_ids = list(ref.stops) if stop_ids is None else stop_ids
    cyc = 2 * o["one_way_min"] + 2 * o["layover_min"]
    return {
        "route_km": o["route_km"],
        "cycle_min": cyc,
        "fleet": o["fleet"],
        "cap_small": fc["bus_12m"],
        "cap_large": fc["bus_18m"],
        "veh_cost_small": o["veh_cost_small"],
        "veh_cost_large": o["veh_cost_large"],
        "comfort": crowd["theta"],                      # legacy proxy uses the same theta
        "crowding": crowd,
        "seg_min": segment_minutes(stop_ids, stop_ll, o["one_way_min"], crowd["segment_times"]),
        "h_min": cyc / o["fleet"],                      # fleet-feasible lower bound
        "h_max": o["headway_max_min"],
    }


def canonical_stop_ids(ref) -> list[str]:
    """Corridor order as published in the stop catalogue (forward direction)."""
    return list(ref.stops)
