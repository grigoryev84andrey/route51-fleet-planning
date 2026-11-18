"""Stage 2 — activity schedules + gravity destination choice -> ground-truth trips.

Each person gets an outbound trip (home -> primary destination), an optional
secondary shop trip, and a return trip home. Destinations are chosen by a gravity
model P(j) ~ W_j^alpha * exp(-beta * km(home, j)). A trip uses Route 51 iff both
ends are within walking distance of a stop and the person chooses the bus; the
board/alight stops are the nearest stops to the trip ends (single route, no
transfers — a deliberate simplification). The result is the ground-truth trip
table from which the cell-level OD and the stop-level bus OD are aggregated.
"""
from __future__ import annotations

import h3
import numpy as np
import pandas as pd

from geo import Corridor

_EARTH = 6_371_000.0

# Which destination classes are eligible for which primary purpose.
_ELIGIBLE = {
    "work": {"work", "work_retail"},
    "education": {"education"},
    "other": {"retail", "work_retail"},
    "retail": {"retail", "work_retail"},
}


def _haversine_matrix(lat1, lon1, lat2, lon2) -> np.ndarray:
    """(n,) x (m,) -> (n,m) metres."""
    la1 = np.radians(np.asarray(lat1))[:, None]
    lo1 = np.radians(np.asarray(lon1))[:, None]
    la2 = np.radians(np.asarray(lat2))[None, :]
    lo2 = np.radians(np.asarray(lon2))[None, :]
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * _EARTH * np.arcsin(np.sqrt(a))


def build_destinations(cfg, corridor: Corridor) -> pd.DataFrame:
    walk = cfg["demand"]["walk_access_m"]
    slat = np.array([s.lat for s in corridor.stops])
    slon = np.array([s.lon for s in corridor.stops])

    rows = []
    for an in cfg["attraction_anchors"]:
        rows.append({"id": an["id"], "lat": an["lat"], "lon": an["lon"],
                     "class": an["class"], "weight": an["weight"]})
    for cz in cfg["study_area"]["cordon_zones"]:
        if cz.get("kind") == "metro_gateway":
            rows.append({"id": cz["id"], "lat": cz["lat"], "lon": cz["lon"],
                         "class": "retail", "weight": 0.5})   # transfer/feeder pull (PLACEHOLDER)
    es = cfg["demand"]["external_sink"]
    rows.append({"id": "EXT_CITY", "lat": es["lat"], "lon": es["lon"],
                 "class": "external", "weight": es["weight"]})

    d = pd.DataFrame(rows)
    dist = _haversine_matrix(d.lat.values, d.lon.values, slat, slon)
    nearest_idx = dist.argmin(1)
    d["nearest_stop"] = [corridor.stops[i].id for i in nearest_idx]
    d["nearest_stop_m"] = dist[np.arange(len(d)), nearest_idx]
    d["bus_acc"] = d["nearest_stop_m"] <= walk
    d.loc[d["id"] == "EXT_CITY", "bus_acc"] = False   # rest-of-city is never a Route-51 end
    d["cell"] = [h3.latlng_to_cell(la, lo, corridor.res) for la, lo in zip(d.lat, d.lon)]
    return d


def _nearest_stops(lats, lons, corridor: Corridor):
    slat = np.array([s.lat for s in corridor.stops])
    slon = np.array([s.lon for s in corridor.stops])
    sid = np.array([s.id for s in corridor.stops])
    dist = _haversine_matrix(lats, lons, slat, slon)
    j = dist.argmin(1)
    return sid[j], dist[np.arange(len(lats)), j]


def _gravity_pick(home_lat, home_lon, dests: pd.DataFrame, alpha, beta, rng):
    dkm = _haversine_matrix(home_lat, home_lon, dests.lat.values, dests.lon.values) / 1000.0
    util = (dests.weight.values[None, :] ** alpha) * np.exp(-beta * dkm)
    util = np.where(util <= 0, 1e-12, util)
    cdf = (util / util.sum(1, keepdims=True)).cumsum(1)
    r = rng.random((len(home_lat), 1))
    return (r < cdf).argmax(1)


def _hour(mins):
    return (np.asarray(mins) // 60).astype(int) % 24


def _make_leg(persons_idx, o_lat, o_lon, o_h3, d_lat, d_lon, d_h3,
              depart_min, purpose, mode, route51, board, alight):
    return pd.DataFrame({
        "person_id": persons_idx, "purpose": purpose, "depart_min": depart_min,
        "hour": _hour(depart_min), "o_lat": o_lat, "o_lon": o_lon, "o_h3": o_h3,
        "d_lat": d_lat, "d_lon": d_lon, "d_h3": d_h3, "mode": mode,
        "route51": route51, "board_stop": board, "alight_stop": alight,
    })


def _assign_mode_route(o_lat, o_lon, d_lat, d_lon, o_acc, d_acc, car_owner, cfg, rng, corridor):
    """Vectorised bus/route decision + board/alight stops for one leg."""
    ms = cfg["demand"]["mode_split"]
    p = np.full(len(o_lat), ms["p_bus_base"], float)
    p = np.where(car_owner, p * ms["car_owner_factor"], p)
    accessible = o_acc & d_acc
    chooses_bus = (rng.random(len(o_lat)) < p) & accessible
    board = np.full(len(o_lat), "", dtype=object)
    alight = np.full(len(o_lat), "", dtype=object)
    if chooses_bus.any():
        bs, _ = _nearest_stops(np.asarray(o_lat)[chooses_bus], np.asarray(o_lon)[chooses_bus], corridor)
        al, _ = _nearest_stops(np.asarray(d_lat)[chooses_bus], np.asarray(d_lon)[chooses_bus], corridor)
        board[chooses_bus] = bs
        alight[chooses_bus] = al
    # A trip whose nearest origin and destination stops coincide is a walk, not a ride.
    degenerate = chooses_bus & (board == alight)
    chooses_bus = chooses_bus & ~degenerate
    board[degenerate] = ""
    alight[degenerate] = ""
    mode = np.where(chooses_bus, "bus", "other")
    return mode, chooses_bus, board, alight


def assign_activities(pop: pd.DataFrame, dests: pd.DataFrame, corridor: Corridor,
                      cfg, rng: np.random.Generator) -> pd.DataFrame:
    dcfg = cfg["demand"]
    alpha = dcfg["gravity"]["attraction_exponent"]
    beta = dcfg["gravity"]["distance_decay_per_km"]
    walk = dcfg["walk_access_m"]

    # Home bus-accessibility (per person).
    _, home_stop_m = _nearest_stops(pop.home_lat.values, pop.home_lon.values, corridor)
    home_acc = home_stop_m <= walk

    legs = []
    # Process per primary purpose so eligibility + gravity vectorise cleanly.
    for purpose in ("work", "education", "other"):
        grp = pop[pop.primary_purpose == purpose]
        if grp.empty:
            continue

        # 'other' persons travel only with discretionary probability.
        if purpose == "other":
            make = rng.random(len(grp)) < dcfg["discretionary_trip_prob"]
            grp = grp[make]
            if grp.empty:
                continue
        pid = grp["person_id"].values   # real P###### ids (join key), positional to grp

        elig = dests[dests["class"].isin(_ELIGIBLE[purpose])].reset_index(drop=True)
        pick = _gravity_pick(grp.home_lat.values, grp.home_lon.values, elig, alpha, beta, rng)
        dd = elig.iloc[pick].reset_index(drop=True)

        # Departure time from the person's schedule window.
        win = np.array([dcfg["departure_windows_min"][s] for s in grp.work_schedule])
        depart = rng.integers(win[:, 0], win[:, 1] + 1)

        ha = home_acc[grp.index.values]
        mode, is_bus, board, alight = _assign_mode_route(
            grp.home_lat.values, grp.home_lon.values, dd.lat.values, dd.lon.values,
            ha, dd.bus_acc.values, grp.car_owner.values, cfg, rng, corridor)

        legs.append(_make_leg(pid, grp.home_lat.values, grp.home_lon.values, grp.home_h3.values,
                              dd.lat.values, dd.lon.values, dd.cell.values, depart,
                              purpose, mode, is_bus, board, alight))

        # Optional secondary shop trip (primary -> retail), then return from there.
        sec_mask = rng.random(len(grp)) < dcfg["secondary_trip_prob"]
        retail = dests[dests["class"].isin({"retail", "work_retail"})].reset_index(drop=True)
        last_lat = dd.lat.values.copy()
        last_lon = dd.lon.values.copy()
        last_h3 = dd.cell.values.copy()
        if sec_mask.any():
            sgrp_lat = dd.lat.values[sec_mask]
            sgrp_lon = dd.lon.values[sec_mask]
            spick = _gravity_pick(sgrp_lat, sgrp_lon, retail, alpha, beta, rng)
            sd = retail.iloc[spick].reset_index(drop=True)
            sdep = rng.integers(*dcfg["secondary_window_min"], size=sec_mask.sum())
            # accessibility of the primary end (origin of the secondary leg)
            _, pm = _nearest_stops(sgrp_lat, sgrp_lon, corridor)
            pacc = pm <= walk
            smode, sbus, sboard, salight = _assign_mode_route(
                sgrp_lat, sgrp_lon, sd.lat.values, sd.lon.values, pacc, sd.bus_acc.values,
                grp.car_owner.values[sec_mask], cfg, rng, corridor)
            legs.append(_make_leg(pid[sec_mask], sgrp_lat, sgrp_lon, dd.cell.values[sec_mask],
                                  sd.lat.values, sd.lon.values, sd.cell.values, sdep,
                                  "shop", smode, sbus, sboard, salight))
            last_lat[sec_mask] = sd.lat.values
            last_lon[sec_mask] = sd.lon.values
            last_h3[sec_mask] = sd.cell.values

        # Return trip: last activity -> home.
        rdep = rng.integers(*dcfg["return_window_min"], size=len(grp))
        _, lm = _nearest_stops(last_lat, last_lon, corridor)
        lacc = lm <= walk
        rmode, rbus, rboard, ralight = _assign_mode_route(
            last_lat, last_lon, grp.home_lat.values, grp.home_lon.values, lacc, ha,
            grp.car_owner.values, cfg, rng, corridor)
        legs.append(_make_leg(pid, last_lat, last_lon, last_h3,
                              grp.home_lat.values, grp.home_lon.values, grp.home_h3.values,
                              rdep, "home", rmode, rbus, rboard, ralight))

    trips = pd.concat(legs, ignore_index=True)
    return trips.sort_values(["person_id", "depart_min"]).reset_index(drop=True)
