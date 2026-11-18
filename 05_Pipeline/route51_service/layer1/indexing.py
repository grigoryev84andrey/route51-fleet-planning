"""H3 spatial indexing (Layer 1).

Snaps every accepted record to the H3 grid (res 9 + macro res 7) and a time bin,
flags in-grid vs external, then builds a per-(cell, time-bin) aggregate. For
PERSONAL feeds, k-anonymity suppression is applied to the aggregate using the
count of DISTINCT PERSONS as the privacy support (cards / subscribers) — NOT row
count. Non-personal feeds (GPS vehicle telemetry) are not suppressed.

The aggregate is the hand-off to Layer 2 (demand estimation).
"""
from __future__ import annotations

from dataclasses import dataclass

import h3
import pandas as pd

from .contracts import FeedContract
from .reference import Reference


@dataclass
class IndexResult:
    indexed: pd.DataFrame
    aggregate: pd.DataFrame
    stats: dict


def _time_bin(hour: int, bins: dict) -> str:
    for name, (lo, hi) in bins.items():
        if lo <= hour < hi:
            return name
    return "unbinned"


def _row_cell(row, contract: FeedContract, ref: Reference) -> str | None:
    res = ref.res
    if contract.coordinate_fields:
        la, lo = contract.coordinate_fields
        return h3.latlng_to_cell(float(row[la]), float(row[lo]), res)
    if contract.h3_field:
        return str(row[contract.h3_field])
    if contract.stop_id_field:
        return ref.stop_cells.get(str(row[contract.stop_id_field]))
    return None


# Distinct-person key + measures to aggregate, per feed.
_PERSON_KEY = {"atto": "hashed_card_id", "telecom": "hashed_subscriber_id"}


def index_feed(df: pd.DataFrame, contract: FeedContract, ref: Reference, cfg) -> IndexResult:
    bins = cfg["spatial"]["time_bins"]
    res_macro = cfg["spatial"]["h3_resolution_macro"]
    df = df.copy()

    ts = pd.to_datetime(df[contract.timestamp_field], errors="coerce")
    df["h3_r9"] = [_row_cell(r, contract, ref) for _, r in df.iterrows()]
    df = df[df["h3_r9"].notna()].copy()
    df["h3_r7"] = df["h3_r9"].map(lambda c: h3.cell_to_parent(c, res_macro))
    df["time_bin"] = ts.dt.hour.map(lambda h: _time_bin(int(h), bins) if pd.notna(h) else "unbinned")
    df["in_grid"] = df["h3_r9"].map(ref.in_grid)

    # Build aggregate per (cell, time_bin).
    grp = df.groupby(["h3_r9", "time_bin"], dropna=False)
    agg = pd.DataFrame({"n_records": grp.size()}).reset_index()

    pkey = _PERSON_KEY.get(contract.name)
    if pkey and pkey in df.columns:
        persons = grp[pkey].nunique().reset_index(name="n_persons")
        agg = agg.merge(persons, on=["h3_r9", "time_bin"])
    if contract.name == "gps":
        extra = grp.agg(
            n_vehicles=("vehicle_id", "nunique"),
            mean_speed_kmh=("speed_kmh", "mean"),
            mean_occupancy=("occupancy", "mean"),
            max_crowding=("crowding_index", "max"),
        ).reset_index()
        agg = agg.merge(extra, on=["h3_r9", "time_bin"])

    agg["in_grid"] = agg["h3_r9"].map(ref.in_grid)

    # k-anonymity suppression (personal feeds only).
    k = cfg["governance"]["k_anonymity"]
    n_suppressed = 0
    n_records_suppressed = 0
    agg["suppressed"] = False
    if contract.is_personal and "n_persons" in agg.columns:
        mask = agg["n_persons"] < k
        n_suppressed = int(mask.sum())
        suppressed_keys = set(map(tuple, agg.loc[mask, ["h3_r9", "time_bin"]].to_numpy()))
        # Null the sensitive counts; keep the row marked so coverage is auditable.
        agg.loc[mask, ["n_records", "n_persons"]] = pd.NA
        agg.loc[mask, "suppressed"] = True

        # Propagate to the row-level hand-off. Suppressing only the published
        # aggregate would leave Layer 2 reading records the privacy policy says
        # were never released, so the guarantee would not bind on the
        # optimisation path (§2.3.2). The flag governs whether Layer 2 sees the
        # suppressed rows; the rows are always marked either way.
        df["k_suppressed"] = [
            (c, t) in suppressed_keys for c, t in zip(df["h3_r9"], df["time_bin"])
        ]
        n_records_suppressed = int(df["k_suppressed"].sum())
        if cfg["governance"].get("suppress_downstream", False):
            df = df[~df["k_suppressed"]].copy()
    else:
        df["k_suppressed"] = False

    stats = {
        "records_indexed": int(len(df)),
        "cells_touched": int(agg["h3_r9"].nunique()),
        "in_grid_share": round(float(df["in_grid"].mean()), 3) if len(df) else 0.0,
        "k_anonymity": k,
        "cells_suppressed": n_suppressed,
        "cells_total": int(len(agg)),
        "records_suppressed": n_records_suppressed,
        "suppression_applies": bool(contract.is_personal),
        "suppress_downstream": bool(cfg["governance"].get("suppress_downstream", False)),
    }
    return IndexResult(indexed=df, aggregate=agg, stats=stats)
