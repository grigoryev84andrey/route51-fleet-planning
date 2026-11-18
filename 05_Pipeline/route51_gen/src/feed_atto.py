"""Feed 2 — ATTO fare tap-ins for Route 51 (tap-IN only, NO alighting).

Derives a boarding event for every Route-51 bus leg in the ground-truth trips.
ATTO is the payment operator (not the carrier); the feed records the boarding
stop and time, never the alighting (alighting blindness is the whole point —
Layer 2 must infer it). Degradations:
  - inactive_card_rate : a fraction of riders never tap (issued != active).
  - p_untapped         : a fraction of boardings go untapped (fare evasion).
  - token_fragmentation: a boarding is logged under a fresh hashed id instead of
                         the rider's stable one (open-loop media switching), which
                         breaks trip-chaining downstream.

Schema matches the Layer-1 ATTO contract; it carries no forbidden field.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def generate_atto(cfg, persons: pd.DataFrame, trips: pd.DataFrame,
                  rng: np.random.Generator) -> pd.DataFrame:
    base = pd.Timestamp(cfg["bus_gps"]["base_date"])
    deg = cfg["degradation"]["atto"]

    bus = (trips[trips["route51"]]
           .merge(persons[["person_id", "atto_card"]], on="person_id", how="left")
           .reset_index(drop=True))

    # Inactive cards: a fraction of distinct riders never tap at all.
    riders = bus["person_id"].unique()
    inactive = set(riders[rng.random(len(riders)) < deg["inactive_card_rate"]])
    bus = bus[~bus["person_id"].isin(inactive)].reset_index(drop=True)

    # Fare evasion: drop a fraction of remaining boardings.
    bus = bus[rng.random(len(bus)) >= deg["p_untapped"]].reset_index(drop=True)

    # Account-level stable hashed id, with token fragmentation overriding some.
    hashed = bus["person_id"].map(lambda p: _sha("card|" + p)).to_numpy()
    frag = rng.random(len(bus)) < deg["token_fragmentation"]
    if frag.any():
        new_ids = [_sha("frag|" + str(int(rng.integers(10**12)))) for _ in range(int(frag.sum()))]
        hashed[frag] = new_ids

    out = pd.DataFrame({
        "hashed_card_id": hashed,
        "timestamp": base + pd.to_timedelta(bus["depart_min"], unit="m"),
        "boarding_stop_id": bus["board_stop"].to_numpy(),
        "route_id": "51",
        "card_type": bus["atto_card"].to_numpy(),
    })
    return out.sort_values("timestamp").reset_index(drop=True)
