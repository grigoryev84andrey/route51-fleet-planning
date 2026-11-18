"""Feed 1 — Telecom CDR/signalling for the observed operator's subscribers.

Derives signalling events (LAU/HO/TA) from the ground-truth trips of the subset
of the population that (a) owns a phone and (b) is on the observed operator —
that subset IS the phone-ownership skew (penetration x single-operator sampling).
Events are denser during travel (handover-heavy) and sparse during stays
(keep-alive). Coarse serving cell only: the feed carries an H3 cell, never raw
coordinates. Degradations:
  - event_rate_multiplier : thin out all events (sparser CDR).
  - p_missing             : drop events (data loss / gaps).
  - p_oscillation         : ping-pong — swap the serving cell for a neighbour;
                            spread is density-scaled (tight in the dense corridor,
                            wide in the sparse periphery).
  - multi_sim             : a fraction of subscribers also emit under a 2nd id.

Schema matches the Layer-1 telecom contract (incl. the in-country tag); it
carries no forbidden field (no lat/lon, no MSISDN/IMSI).
"""
from __future__ import annotations

import hashlib

import h3
import numpy as np
import pandas as pd

from geo import Corridor, haversine_m

_REGION = "UZ"   # in-country processing tag [Art. 27-1]; stamped by the operator side


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _assign_usage(n: int, sig, rng) -> np.ndarray:
    names = list(sig["usage_intensity_multiplier"])
    base = {"light": 0.30, "medium": 0.50, "heavy": 0.20}      # PLACEHOLDER split
    p = np.array([base.get(k, 1.0 / len(names)) for k in names], float)
    p = p / p.sum()
    return np.asarray(names)[rng.choice(len(names), size=n, p=p)]


def _expand(rows_sub, rows_min, rows_cell, rows_type):
    return (np.concatenate(rows_sub), np.concatenate(rows_min),
            np.concatenate(rows_cell), np.concatenate(rows_type))


def generate_telecom(cfg, persons: pd.DataFrame, trips: pd.DataFrame,
                     corridor: Corridor, rng: np.random.Generator) -> pd.DataFrame:
    tel, sig = cfg["telecom"], cfg["telecom"]["signalling"]
    deg = cfg["degradation"]["telecom"]
    res = cfg["study_area"]["h3_resolution"]
    base = pd.Timestamp(cfg["bus_gps"]["base_date"])
    erm = deg["event_rate_multiplier"]
    evt = sig["event_types"]
    r_stay, r_travel = sig["base_rate_stay_per_hr"], sig["base_rate_travel_per_hr"]

    # --- observed-operator subscribers (the phone-ownership skew) ---
    obs = persons[(persons["phone_owner"]) &
                  (persons["operator"] == tel["observed_operator"])].copy()
    obs["umult"] = pd.Series(_assign_usage(len(obs), sig, rng), index=obs.index).map(
        sig["usage_intensity_multiplier"])
    obs["sub_id"] = obs["person_id"].map(lambda p: _sha("sub|" + p))

    tr = trips.merge(obs[["person_id", "sub_id", "umult"]], on="person_id", how="inner").copy()
    dkm = (np.array([haversine_m(a, b, c, d) for a, b, c, d in
                     zip(tr.o_lat, tr.o_lon, tr.d_lat, tr.d_lon)]) / 1000.0)
    travel_min = (dkm / cfg["demand"]["travel_speed_kmh"] * 60.0)
    tr["arrive_min"] = np.minimum(1439, (tr["depart_min"].to_numpy() + travel_min).round()).astype(int)
    tr = tr.sort_values(["person_id", "depart_min"]).reset_index(drop=True)

    sub_a, min_a, cell_a, type_a = [], [], [], []

    # --- TRAVEL events (handover-heavy), cell interpolated along the leg ---
    dur_hr = np.clip(tr["arrive_min"] - tr["depart_min"], 1, None) / 60.0
    cnt = rng.poisson(r_travel * dur_hr.to_numpy() * tr["umult"].to_numpy() * erm)
    idx = np.repeat(np.arange(len(tr)), cnt)
    if idx.size:
        d0, d1 = tr["depart_min"].to_numpy()[idx], tr["arrive_min"].to_numpy()[idx]
        et = d0 + rng.random(idx.size) * (d1 - d0)
        f = np.where(d1 > d0, (et - d0) / np.maximum(d1 - d0, 1), 0.0)
        la = tr.o_lat.to_numpy()[idx] + (tr.d_lat.to_numpy()[idx] - tr.o_lat.to_numpy()[idx]) * f
        lo = tr.o_lon.to_numpy()[idx] + (tr.d_lon.to_numpy()[idx] - tr.o_lon.to_numpy()[idx]) * f
        cells = np.array([h3.latlng_to_cell(a, b, res) for a, b in zip(la, lo)])
        types = np.asarray(evt)[rng.choice(len(evt), size=idx.size, p=[0.20, 0.60, 0.20])]
        sub_a.append(tr.sub_id.to_numpy()[idx]); min_a.append(et)
        cell_a.append(cells); type_a.append(types)

    # --- STAY events (keep-alive), at a fixed serving cell ---
    g = tr.groupby("person_id", sort=False)
    next_dep = g["depart_min"].shift(-1).fillna(1440).to_numpy()
    first = g.first().reset_index()
    s_start = np.concatenate([tr["arrive_min"].to_numpy(), np.zeros(len(first))])
    s_end = np.concatenate([next_dep, first["depart_min"].to_numpy()])
    s_cell = np.concatenate([tr["d_h3"].to_numpy(), first["o_h3"].to_numpy()])
    s_sub = np.concatenate([tr["sub_id"].to_numpy(), first["sub_id"].to_numpy()])
    s_um = np.concatenate([tr["umult"].to_numpy(), first["umult"].to_numpy()])
    dur_s = np.clip(s_end - s_start, 0, None) / 60.0
    cnt_s = rng.poisson(r_stay * dur_s * s_um * erm)
    idxs = np.repeat(np.arange(len(s_start)), cnt_s)
    if idxs.size:
        ts = s_start[idxs] + rng.random(idxs.size) * (s_end[idxs] - s_start[idxs])
        types_s = np.asarray(evt)[rng.choice(len(evt), size=idxs.size, p=[0.50, 0.10, 0.40])]
        sub_a.append(s_sub[idxs]); min_a.append(ts)
        cell_a.append(s_cell[idxs]); type_a.append(types_s)

    sub, tmin, cell, typ = _expand(sub_a, min_a, cell_a, type_a)
    df = pd.DataFrame({"hashed_subscriber_id": sub, "event_type": typ,
                       "_min": tmin, "h3_cell": cell})

    # --- degradations ---
    if deg["p_missing"] > 0:
        df = df[rng.random(len(df)) >= deg["p_missing"]].reset_index(drop=True)

    if deg["p_oscillation"] > 0:
        osc = rng.random(len(df)) < deg["p_oscillation"]
        if osc.any():
            grid = corridor.internal_cells
            kd, ks = deg["osc_spread_dense_k"], deg["osc_spread_sparse_k"]
            cells = df["h3_cell"].to_numpy()
            for i in np.where(osc)[0]:
                k = kd if cells[i] in grid else ks
                ring = list(h3.grid_disk(cells[i], k))
                cells[i] = ring[int(rng.integers(len(ring)))]
            df["h3_cell"] = cells

    if tel["multi_sim"]["enabled"]:
        subs = df["hashed_subscriber_id"].unique()
        dup = subs[rng.random(len(subs)) < tel["multi_sim"]["secondary_sim_rate"]]
        extra = df[df["hashed_subscriber_id"].isin(dup)].copy()
        extra["hashed_subscriber_id"] = extra["hashed_subscriber_id"].map(lambda s: _sha("sim2|" + s))
        df = pd.concat([df, extra], ignore_index=True)

    df["timestamp"] = base + pd.to_timedelta(df["_min"], unit="m")
    df["processing_region"] = _REGION
    return (df[["hashed_subscriber_id", "event_type", "timestamp", "h3_cell", "processing_region"]]
            .sort_values("timestamp").reset_index(drop=True))
