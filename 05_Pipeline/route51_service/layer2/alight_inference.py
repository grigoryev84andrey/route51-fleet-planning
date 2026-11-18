"""Layer 2 — Alighting inference from ATTO (trip-chaining).

ATTO records only boardings. Trip-chaining recovers partial alighting evidence:
  - the alighting stop of a card's trip k ≈ the boarding stop of its trip k+1;
  - the alighting stop of the last trip of the day ≈ the first boarding of the day
    (return-home closure).
This only works for cards with >= 2 taps in the day; single-tap riders contribute
no chaining evidence and rely on the telecom prior in fusion. Token fragmentation
(a person split across hashed ids) silently breaks chains — which is exactly why
that degradation knob is a validation axis.
"""
from __future__ import annotations

import pandas as pd


def chain_alightings(atto_indexed: pd.DataFrame) -> pd.DataFrame:
    df = atto_indexed[["hashed_card_id", "timestamp", "boarding_stop_id"]].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values(["hashed_card_id", "timestamp"])
    df["hour"] = df["timestamp"].dt.hour

    g = df.groupby("hashed_card_id")
    df["next_board"] = g["boarding_stop_id"].shift(-1)

    # Inter-trip chaining: alight(k) ~ board(k+1).
    inter = df[df["next_board"].notna()].copy()
    inter["alight_stop"] = inter["next_board"]

    # Day closure: last trip's alight ~ first boarding of the day.
    first_board = g["boarding_stop_id"].first()
    last = df[df["next_board"].isna()].copy()
    last["alight_stop"] = last["hashed_card_id"].map(first_board)

    chained = pd.concat([inter, last], ignore_index=True)
    chained = chained[chained["alight_stop"] != chained["boarding_stop_id"]]  # drop degenerate

    evidence = (chained.groupby(["boarding_stop_id", "alight_stop", "hour"])
                .size().reset_index(name="c"))
    return evidence
