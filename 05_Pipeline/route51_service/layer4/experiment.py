"""Layer 4 — registered simulation experiment (protocol route51_v2 v1.0).

One run = one condition (a dispatch timetable) under one seed:

  * the network has both directions (layer4.network.build_network_bidir);
  * every dispatch trip of both directions with planned departure in
    [06:30, 11:30) is inserted at its planned second, with its own vehicle type;
  * dwell at a stop = 5 s + 1.5 s/pax * (1 - speedup * is_18m) * (boardings +
    alightings per trip at that stop, by direction and period), times lognormal
    noise (sigma 0.25), at least 3 s; the noise stream is
    default_rng(SeedSequence([seed, 2]));
  * background traffic from randomTrips.py (--seed = run seed) over
    [06:30, 11:30); a missing tool is an error, never a silent run without it;
  * SUMO --seed = run seed; the run ends at the guard 11:30.

The measured cohort is every trip with planned departure in [07:00, 10:00).
A run is valid only if each cohort bus was inserted, arrived by the guard, and
was neither teleported nor removed. Background teleports are counted, not used
to exclude runs. Partial durations of unfinished buses are never used.
"""
from __future__ import annotations

import gzip
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from layer3.dispatch import Trip
from layer3.operations import PERIODS

SIM_BEGIN_S = 6 * 3600 + 30 * 60
GUARD_END_S = 11 * 3600 + 30 * 60
EXPERIMENT_VERSION = "sumo_experiment_v1"


class RunInvalid(Exception):
    pass


# --------------------------------------------------------------------------- #
# Demand at stops, by direction and period
# --------------------------------------------------------------------------- #
def stop_activity(fused_scaled: pd.DataFrame, stop_ids: list[str]) -> dict:
    """{(period, dir): {stop: boardings + alightings}} over the whole period."""
    pos = {s: i for i, s in enumerate(stop_ids)}
    hour2period = {h: p for p, hs in PERIODS.items() for h in hs}
    f = fused_scaled.copy()
    f["period"] = f["hour"].map(hour2period)
    f["dir"] = np.where(f["board_stop"].map(pos) < f["alight_stop"].map(pos), "fwd", "rev")
    out = {}
    for (per, d), g in f.groupby(["period", "dir"]):
        act = g.groupby("board_stop")["demand"].sum().add(
            g.groupby("alight_stop")["demand"].sum(), fill_value=0.0)
        out[(per, d)] = act.to_dict()
    return out


def am_od(fused_scaled: pd.DataFrame, stop_ids: list[str]) -> pd.DataFrame:
    """Morning-peak OD demand (hours 7-9) with direction, for passenger-time derivation."""
    pos = {s: i for i, s in enumerate(stop_ids)}
    f = fused_scaled[fused_scaled["hour"].isin(PERIODS["am_peak"])].copy()
    f["dir"] = np.where(f["board_stop"].map(pos) < f["alight_stop"].map(pos), "fwd", "rev")
    return f.groupby(["dir", "board_stop", "alight_stop"], as_index=False)["demand"].sum()


# --------------------------------------------------------------------------- #
# Route file
# --------------------------------------------------------------------------- #
def trips_in_window(trips: list[Trip], begin=SIM_BEGIN_S, end=GUARD_END_S) -> list[Trip]:
    return sorted([t for t in trips if begin <= t.planned_dep_s < end],
                  key=lambda t: (t.planned_dep_s, t.trip_id))


def write_bus_routes(trips_all: list[Trip], activity: dict, edges: dict, bstops: dict,
                     sim: dict, speedup: float, seed: int, path: Path) -> dict:
    """Write the bus route file for one run; return planned and cohort ids."""
    rng = np.random.default_rng(np.random.SeedSequence([int(seed), 2]))
    n_trips = {}
    for t in trips_all:
        n_trips[(t.period, t.direction)] = n_trips.get((t.period, t.direction), 0) + 1
    sim_trips = trips_in_window(trips_all)
    base, perpax = float(sim["dwell_base_s"]), float(sim["dwell_per_pax_s"])
    R = ["<routes>",
         '  <vType id="bus12" vClass="bus" length="12" personCapacity="90" accel="1.0" decel="3.0">'
         '</vType>',
         '  <vType id="bus18" vClass="bus" length="18" personCapacity="150" accel="1.0" decel="3.0">'
         '</vType>',
         f'  <route id="fwd" edges="{" ".join(edges["fwd"])}"/>',
         f'  <route id="rev" edges="{" ".join(edges["rev"])}"/>']
    for t in sim_trips:
        big = t.vtype == "bus18"
        pp = perpax * (1.0 - speedup * (1.0 if big else 0.0))
        act = activity.get((t.period, t.direction), {})
        n = n_trips[(t.period, t.direction)]
        stops_xml = []
        for s, bid in bstops[t.direction]:
            d = base + pp * act.get(s, 0.0) / n
            d = max(3.0, d * rng.lognormal(0.0, 0.25))
            stops_xml.append(f'<stop busStop="{bid}" duration="{d:.2f}"/>')
        R.append(f'  <vehicle id="{t.trip_id}" type="{t.vtype}" route="{t.direction}" '
                 f'depart="{t.planned_dep_s}" departLane="0">{"".join(stops_xml)}</vehicle>')
    R.append("</routes>")
    path.write_text("\n".join(R), encoding="utf-8")
    return {"planned": [t.trip_id for t in sim_trips],
            "cohort": [t.trip_id for t in sim_trips if t.cohort],
            "trips": {t.trip_id: t for t in sim_trips}}


# --------------------------------------------------------------------------- #
# Running and parsing
# --------------------------------------------------------------------------- #
def run_sumo(net: Path, add: Path, wd: Path, routes: list[Path], tag: str, seed: int,
             teleport_s: int) -> dict:
    """Run one case in its own directory `wd`; network and stops are shared, read-only."""
    rfiles = ",".join(str(Path(r).resolve()) for r in routes)
    cfg = f"""<configuration>
 <input><net-file value="{Path(net).resolve()}"/><route-files value="{rfiles}"/><additional-files value="{Path(add).resolve()}"/></input>
 <time><begin value="{SIM_BEGIN_S}"/><end value="{GUARD_END_S}"/></time>
 <output>
  <tripinfo-output value="{tag}.ti.xml.gz"/>
  <tripinfo-output.write-unfinished value="true"/>
  <tripinfo-output.write-undeparted value="true"/>
  <stop-output value="{tag}.stops.xml.gz"/>
 </output>
 <processing><time-to-teleport value="{teleport_s}"/></processing>
 <random_number><seed value="{int(seed)}"/></random_number>
 <report><no-step-log value="true"/><duration-log.disable value="true"/><error-log value="{tag}.errors.log"/></report>
</configuration>"""
    (wd / f"{tag}.sumocfg").write_text(cfg, encoding="utf-8")
    t0 = time.time()
    r = subprocess.run(["sumo", "-c", f"{tag}.sumocfg"], cwd=str(wd), capture_output=True, text=True)
    if r.returncode != 0:
        raise RunInvalid(f"SUMO failed: {(r.stdout + r.stderr)[-800:]}")
    return {"seconds": time.time() - t0}


_TELEPORT = re.compile(r"Teleporting vehicle '([^']+)'")
_REMOVE = re.compile(r"(?:Removing|removed|Vehicle) '([^']+)'[^\n]*(?:remov|vaporiz)", re.I)


def parse_run(wd: Path, tag: str, ids: dict) -> dict:
    with gzip.open(wd / f"{tag}.ti.xml.gz", "rb") as fh:
        root = ET.parse(fh).getroot()
    ti = {}
    for e in root.findall("tripinfo"):
        ti[e.get("id")] = {"depart": float(e.get("depart", -1)),
                           "departDelay": float(e.get("departDelay", 0) or 0),
                           "arrival": float(e.get("arrival", -1)),
                           "duration": float(e.get("duration", -1)),
                           "vType": e.get("vType")}
    log = (wd / f"{tag}.errors.log").read_text(encoding="utf-8", errors="replace") \
        if (wd / f"{tag}.errors.log").exists() else ""
    tele = set(_TELEPORT.findall(log))
    removed = set(_REMOVE.findall(log))

    planned, cohort = set(ids["planned"]), set(ids["cohort"])
    inserted = {v for v, r in ti.items() if v in planned and r["depart"] >= 0}
    arrived = {v for v, r in ti.items() if v in planned and r["arrival"] >= 0}
    missing = planned - set(ti)
    unfinished = {v for v in planned if v in ti and ti[v]["depart"] >= 0 and ti[v]["arrival"] < 0}
    undeparted = {v for v in planned if v in ti and ti[v]["depart"] < 0}
    bus_tele = tele & planned
    bus_removed = removed & planned
    bg_tele = len(tele - planned)

    # stop times of buses (for terminal regularity and passenger-time derivation)
    stops = []
    with gzip.open(wd / f"{tag}.stops.xml.gz", "rb") as fh:
        sroot = ET.parse(fh).getroot()
    for e in sroot.findall("stopinfo"):
        v = e.get("id")
        if v in cohort:
            stops.append({"trip_id": v, "busStop": e.get("busStop"),
                          "started": float(e.get("started")), "ended": float(e.get("ended"))})

    problems = []
    for name, sset in (("cohort_not_inserted", (cohort - inserted)),
                       ("cohort_not_arrived", (cohort - arrived)),
                       ("cohort_teleported", cohort & bus_tele),
                       ("cohort_removed", cohort & bus_removed),
                       ("cohort_missing_from_output", cohort & missing)):
        if sset:
            problems.append({"code": name, "trip_ids": sorted(sset)})

    buses = []
    trips = ids["trips"]
    for v in sorted(cohort):
        r = ti.get(v, {})
        t = trips[v]
        buses.append({"trip_id": v, "direction": t.direction, "vtype": t.vtype,
                      "planned_dep_s": t.planned_dep_s,
                      "actual_dep_s": r.get("depart"), "depart_delay_s": r.get("departDelay"),
                      "arrival_s": r.get("arrival"), "duration_s": r.get("duration"),
                      "teleported": v in bus_tele, "removed": v in bus_removed})
    return {
        "valid": not problems,
        "problems": problems,
        "id_sets": {"planned": len(planned), "cohort": len(cohort), "inserted": len(inserted),
                    "arrived": len(arrived), "missing": len(missing), "unfinished": len(unfinished),
                    "undeparted": len(undeparted), "teleported_buses": len(bus_tele),
                    "removed_buses": len(bus_removed)},
        "background_teleports": bg_tele,
        "buses": buses,
        "stops": stops,
    }


# --------------------------------------------------------------------------- #
# Per-run outcome
# --------------------------------------------------------------------------- #
def _cv(x):
    x = np.sort(np.asarray(x, float))
    if x.size < 3:
        return None
    d = np.diff(x)
    return float(d.std() / d.mean()) if d.mean() > 0 else None


def run_outcome(parsed: dict, bstops: dict, od_am: pd.DataFrame) -> dict:
    """Primary value and secondary measures of one run (only for valid runs)."""
    b = pd.DataFrame(parsed["buses"])
    out = {"valid": parsed["valid"]}
    if not parsed["valid"] or b.empty:
        out["primary_min"] = None
        return out
    b["duration_min"] = b["duration_s"] / 60.0
    means = b.groupby("direction")["duration_min"].mean().to_dict()
    counts = b.groupby("direction").size().to_dict()
    out.update({
        "mean_duration_fwd_min": means.get("fwd"),
        "mean_duration_rev_min": means.get("rev"),
        "n_fwd": int(counts.get("fwd", 0)), "n_rev": int(counts.get("rev", 0)),
        "primary_min": (0.5 * means["fwd"] + 0.5 * means["rev"]
                        if "fwd" in means and "rev" in means else None),
        "mean_depart_delay_s": float(b["depart_delay_s"].mean()),
        "max_depart_delay_s": float(b["depart_delay_s"].max()),
        "terminal_headway_cv_fwd": _cv(b[b.direction == "fwd"]["arrival_s"]),
        "terminal_headway_cv_rev": _cv(b[b.direction == "rev"]["arrival_s"]),
    })
    # Passenger in-vehicle time (derived): mean over cohort buses of
    # (arrival at the alighting stop - departure from the boarding stop),
    # weighted by morning-peak OD demand of the same direction.
    s = pd.DataFrame(parsed["stops"])
    total = 0.0
    ok = not s.empty
    if ok:
        stop_of = {bid: st for d in bstops for st, bid in bstops[d]}
        s["stop"] = s["busStop"].map(stop_of)
        s["direction"] = s["trip_id"].map(dict(zip(b["trip_id"], b["direction"])))
        for d in ("fwd", "rev"):
            sd = s[s.direction == d]
            if sd.empty:
                ok = False
                break
            ended = sd.groupby("stop")["ended"].mean()
            started = sd.groupby("stop")["started"].mean()
            od = od_am[od_am["dir"] == d]
            t = (started.reindex(od["alight_stop"]).to_numpy()
                 - ended.reindex(od["board_stop"]).to_numpy()) / 60.0
            if np.isnan(t).any():
                ok = False
                break
            total += float((od["demand"].to_numpy() * t).sum())
    out["pax_ivt_am_min"] = total if ok else None
    return out
