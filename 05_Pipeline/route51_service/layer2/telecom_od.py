"""Layer 2 — Telecom OD matrix.

Builds a coarse origin-destination matrix from telecom CDR. Uses the robust
"modal cell per (subscriber, hour)" method (standard for noisy CDR mobility): the
most-frequent serving cell in each hour is the subscriber's location that hour; a
trip is recorded whenever that cell changes between consecutive hours. This is
robust to ping-pong oscillation but coarse in time (sub-hour trips are missed) —
a deliberate simplification.

The output is at H3-cell resolution; only the normalised destination *shape* is
later used in fusion, so the (uncertain) expansion factor cancels out.
"""
from __future__ import annotations

import pandas as pd


def build_telecom_od(telecom_indexed: pd.DataFrame, sample_rate: float) -> pd.DataFrame:
    df = telecom_indexed[["hashed_subscriber_id", "timestamp", "h3_r9"]].copy()
    df["hour"] = pd.to_datetime(df["timestamp"]).dt.hour

    # Modal serving cell per (subscriber, hour): count events per cell, keep the top.
    cnt = (df.groupby(["hashed_subscriber_id", "hour", "h3_r9"])
             .size().reset_index(name="c")
             .sort_values(["hashed_subscriber_id", "hour", "c"], ascending=[True, True, False]))
    modal = cnt.drop_duplicates(["hashed_subscriber_id", "hour"], keep="first")
    modal = modal.sort_values(["hashed_subscriber_id", "hour"])

    # A trip = a change of modal cell between consecutive hours for the same subscriber.
    g = modal.groupby("hashed_subscriber_id")
    modal["o_cell"] = g["h3_r9"].shift()
    trips = modal[modal["o_cell"].notna() & (modal["o_cell"] != modal["h3_r9"])]

    od = (trips.groupby(["o_cell", "h3_r9", "hour"]).size()
          .reset_index(name="n").rename(columns={"h3_r9": "d_cell"}))
    od["n_expanded"] = od["n"] / max(sample_rate, 1e-6)
    return od
