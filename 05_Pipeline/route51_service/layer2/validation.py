"""Layer 2 — validation of the fused OD against the ground-truth bus OD (oracle).

Reports recovery quality, separating the easy part (boarding margins, which ATTO
pins directly) from the hard part (the inferred alighting distribution). Designed
to be reused across a degradation sweep: call `validate()` per run and tabulate
the metrics vs. the degradation level.

Inputs are checked before scoring: missing columns, missing values, non-integer or
out-of-range hours, negative demand, duplicate OD keys and (when `known_stops` is
given) stops outside the reference list raise `ValidationInputError`. Metrics that
are undefined for the given inputs (e.g. an all-zero oracle) are returned as None
with a code in `reason_codes`, never as 0.
"""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

VALIDATION_VERSION = "layer2_validate_v2"

KEYS = ["board_stop", "alight_stop", "hour"]


class ValidationInputError(ValueError):
    """The fused matrix or the oracle cannot be scored as given."""


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na > 0 and nb > 0 else np.nan


def _check(df: pd.DataFrame, value_col: str, name: str,
           known_stops: set | None) -> None:
    missing = [c for c in KEYS + [value_col] if c not in df.columns]
    if missing:
        raise ValidationInputError(f"{name}: missing columns {missing}")
    na = df[KEYS + [value_col]].isna().sum()
    if na.any():
        raise ValidationInputError(f"{name}: missing values {na[na > 0].to_dict()}")
    hours = pd.to_numeric(df["hour"], errors="coerce")
    if hours.isna().any() or not np.all(np.isfinite(hours)) \
            or (hours != np.round(hours)).any() or (hours < 0).any() or (hours > 23).any():
        raise ValidationInputError(f"{name}: hour must be an integer in 0..23")
    vals = pd.to_numeric(df[value_col], errors="coerce")
    if vals.isna().any() or not np.all(np.isfinite(vals)):
        raise ValidationInputError(f"{name}: non-numeric or non-finite {value_col}")
    if (vals < 0).any():
        raise ValidationInputError(f"{name}: negative {value_col} in {int((vals < 0).sum())} rows")
    dup = df.duplicated(subset=KEYS).sum()
    if dup:
        raise ValidationInputError(f"{name}: {int(dup)} duplicate (board_stop, alight_stop, hour) keys")
    if known_stops is not None:
        unknown = (set(df["board_stop"]) | set(df["alight_stop"])) - known_stops
        if unknown:
            raise ValidationInputError(f"{name}: unknown stops {sorted(unknown)}")


def validate(fused: pd.DataFrame, oracle: pd.DataFrame, *,
             known_stops: Iterable[str] | None = None,
             full_precision: bool = False) -> dict:
    """Score `fused` (column `demand`) against `oracle` (column `riders`).

    Returns (metrics, aligned). With `full_precision=False` (the historical
    behaviour) metric values are rounded for display; with True they are not.
    """
    ks = set(known_stops) if known_stops is not None else None
    _check(fused, "demand", "fused", ks)
    _check(oracle, "riders", "oracle", ks)

    f = fused.rename(columns={"demand": "fused"})
    o = oracle.rename(columns={"riders": "truth"})
    m = (f.merge(o, on=KEYS, how="outer")
         .fillna({"fused": 0.0, "truth": 0.0}))

    fused_v, truth_v = m["fused"].to_numpy(), m["truth"].to_numpy()
    reasons: list[str] = []

    # OD-cell errors.
    mae = float(np.mean(np.abs(fused_v - truth_v))) if len(m) else None
    rmse = float(np.sqrt(np.mean((fused_v - truth_v) ** 2))) if len(m) else None
    if not len(m):
        reasons.append("no_od_cells")
    tot_fused, tot_truth = float(fused_v.sum()), float(truth_v.sum())
    if tot_truth > 0:
        total_error_pct = 100 * (tot_fused - tot_truth) / tot_truth
    else:
        total_error_pct = None
        reasons.append("oracle_total_zero")

    # Boarding-margin error (per board_stop x hour).
    bf = m.groupby(["board_stop", "hour"])["fused"].sum()
    bt = m.groupby(["board_stop", "hour"])["truth"].sum()
    board_mae = float((bf - bt).abs().mean()) if len(bf) else None

    # Alighting distribution similarity, per origin, weighted by boardings.
    sims, weights = [], []
    for (i, h), sub in m.groupby(["board_stop", "hour"]):
        t = sub["truth"].to_numpy()
        if t.sum() <= 0:
            continue  # no ground-truth demand for this origin -> nothing to score
        sim = _cosine(sub["fused"].to_numpy(), t)
        if not np.isfinite(sim):
            sim = 0.0  # fused all-zero against real demand = total miss (honest, not skipped)
        sims.append(sim)
        weights.append(t.sum())
    if sims:
        alight_cos = float(np.average(sims, weights=weights))
    else:
        alight_cos = None
        reasons.append("no_scored_origins")

    # Top-K OD recall (are the biggest true flows captured?).
    K = 20
    top_truth = set(map(tuple, o[o["truth"] > 0].sort_values("truth", ascending=False)
                        .head(K)[KEYS].to_numpy()))
    top_fused = set(map(tuple, f.sort_values("fused", ascending=False)
                        .head(K)[KEYS].to_numpy()))
    if top_truth:
        recall = len(top_truth & top_fused) / len(top_truth)
    else:
        recall = None
        reasons.append("oracle_has_no_positive_flows")

    def r(x, nd):
        return x if (full_precision or x is None) else round(x, nd)

    return {
        "od_cells": int(len(m)),
        "total_fused": r(tot_fused, 1),
        "total_truth": r(tot_truth, 1),
        "total_error_pct": r(total_error_pct, 1),
        "od_mae": r(mae, 3),
        "od_rmse": r(rmse, 3),
        "boarding_mae": r(board_mae, 3),
        "alight_cosine_wtd": r(alight_cos, 3),
        f"top{K}_od_recall": r(recall, 3),
        "validation_version": VALIDATION_VERSION,
        "reason_codes": reasons,
    }, m
