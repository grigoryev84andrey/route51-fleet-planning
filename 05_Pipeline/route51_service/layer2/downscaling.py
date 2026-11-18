"""Layer 2 — Bayesian downscaling / data fusion -> fused stop-level OD.

Fuses three signals into a stop-level OD d[board, alight, hour]:
  * ATTO boardings           -> row totals B[i,h] (the boarding margin).
  * Telecom OD (mapped stops)-> Dirichlet PRIOR on the destination split p[i,.,h].
  * Trip-chaining evidence   -> Dirichlet DATA counts c[i,.,h].

Conjugate update (per origin stop i, hour h):
    posterior unnormalised  u[i,j,h] = alpha * p[i,j,h] + c[i,j,h] + eps   (j != i)
    destination split       theta[i,j,h] = u / sum_j u
    fused demand            d[i,j,h] = B[i,h] * theta[i,j,h]

`alpha` is the prior strength, `eps` a uniform base so origins with neither prior
nor evidence still allocate. The whole step is exact and fast (no MCMC); a full
PyMC posterior is a drop-in extension if interval estimates are needed.
"""
from __future__ import annotations

import math

import h3
import pandas as pd

from layer1.reference import Reference

_EARTH = 6_371_000.0


def _haversine(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2)
    a = (math.sin(math.radians(la2 - la1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2)
    return 2 * _EARTH * math.asin(math.sqrt(a))


def _cell_to_nearest_stop(cells, ref: Reference, walk_m: float) -> dict:
    """Map each H3 cell to its nearest stop within walk_m (else None)."""
    out = {}
    stop_items = list(ref.stops.items())
    for c in cells:
        la, lo = h3.cell_to_latlng(c)
        best, bestd = None, 1e18
        for sid, (sla, slo) in stop_items:
            d = _haversine(la, lo, sla, slo)
            if d < bestd:
                best, bestd = sid, d
        out[c] = best if bestd <= walk_m else None
    return out


def fuse(atto_indexed: pd.DataFrame, telecom_od: pd.DataFrame, chain_evidence: pd.DataFrame,
         ref: Reference, walk_m: float, alpha: float = 5.0, eps: float = 0.01) -> pd.DataFrame:
    stops = list(ref.stops.keys())

    # --- Boarding margins B[i,h] from ATTO ---
    a = atto_indexed[["boarding_stop_id", "timestamp"]].copy()
    a["hour"] = pd.to_datetime(a["timestamp"]).dt.hour
    B = (a.groupby(["boarding_stop_id", "hour"]).size()
         .reset_index(name="B").rename(columns={"boarding_stop_id": "i"}))

    # --- Telecom destination-share prior (cell OD -> stops -> shares per origin/hour) ---
    cells = set(telecom_od["o_cell"]) | set(telecom_od["d_cell"])
    c2s = _cell_to_nearest_stop(cells, ref, walk_m)
    t = telecom_od.copy()
    t["i"] = t["o_cell"].map(c2s)
    t["j"] = t["d_cell"].map(c2s)
    t = t.dropna(subset=["i", "j"])
    t = t[t["i"] != t["j"]]
    prior = (t.groupby(["i", "j", "hour"])["n_expanded"].sum().reset_index(name="w"))
    psum = prior.groupby(["i", "hour"])["w"].transform("sum")
    prior["prior_share"] = prior["w"] / psum

    # --- Build the full (i, j, h) grid over origins that have boardings ---
    hours = sorted(B["hour"].unique())
    idx = pd.MultiIndex.from_product(
        [B["i"].unique(), stops, hours], names=["i", "j", "hour"]).to_frame(index=False)
    idx = idx[idx["i"] != idx["j"]]

    grid = (idx.merge(prior[["i", "j", "hour", "prior_share"]], on=["i", "j", "hour"], how="left")
            .merge(chain_evidence.rename(columns={"boarding_stop_id": "i", "alight_stop": "j"}),
                   on=["i", "j", "hour"], how="left")
            .merge(B, on=["i", "hour"], how="left"))
    grid["prior_share"] = grid["prior_share"].fillna(0.0)
    grid["c"] = grid["c"].fillna(0.0)
    grid = grid[grid["B"].notna()]

    # --- Conjugate posterior split + fused demand ---
    grid["u"] = alpha * grid["prior_share"] + grid["c"] + eps
    usum = grid.groupby(["i", "hour"])["u"].transform("sum")
    grid["theta"] = grid["u"] / usum
    grid["demand"] = grid["B"] * grid["theta"]

    fused = (grid[grid["demand"] > 1e-9][["i", "j", "hour", "demand"]]
             .rename(columns={"i": "board_stop", "j": "alight_stop"})
             .sort_values("demand", ascending=False).reset_index(drop=True))
    return fused
