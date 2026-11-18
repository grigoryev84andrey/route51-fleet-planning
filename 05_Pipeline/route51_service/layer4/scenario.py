"""Layer 4 — SUMO scenario builder.

Turns a service plan (per-period headway + 18 m share) and the fused demand into a
SUMO route file: individual bus vehicles departing the terminal at the plan's
headways, each stopping at every bus stop with a dwell time driven by demand
(boardings + alightings per visit) and randomised (lognormal) so headway
irregularity can propagate. 18 m buses board faster (more doors -> lower per-pax
dwell), which is how the capacity reallocation shows up in vehicle dynamics.
Vehicles are written sorted by departure (SUMO requires it).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

PERIOD_HOURS = {
    "am_peak": [7, 8, 9], "midday": [10, 11, 12, 13, 14, 15],
    "pm_peak": [16, 17, 18], "off_peak": [5, 6, 19, 20, 21, 22],
}
WINDOWS = {
    "am_peak": [(7, 10)], "midday": [(10, 16)],
    "pm_peak": [(16, 19)], "off_peak": [(5, 7), (19, 23)],
}


def period_demand(fused, stops):
    out = {}
    for per, hrs in PERIOD_HOURS.items():
        fp = fused[fused["hour"].isin(hrs)]
        out[per] = (fp.groupby("board_stop")["demand"].sum(),
                    fp.groupby("alight_stop")["demand"].sum())
    return out


def build_routes(plan, demand, edges, stops, params, wd, fname, seed=1):
    """plan: {period: (headway_min, share_18m)}."""
    rng = np.random.default_rng(seed)
    base, perpax = params["dwell_base_s"], params["dwell_per_pax_s"]
    speedup = params["boarding_speedup_18m"]

    vehicles = []   # (depart, id, vtype, stops_xml)
    for per, (H, share) in plan.items():
        board, alight = demand[per]
        dur_min = len(PERIOD_HOURS[per]) * 60
        trips = max(dur_min / H, 1)
        pp = perpax * (1 - speedup * share)
        dwell = {s: max(base, base + pp * ((board.get(s, 0) + alight.get(s, 0)) / trips))
                 for s in stops}
        vtype = "bus18" if share >= 0.5 else "bus12"
        i = 0
        for (h0, h1) in WINDOWS[per]:
            t = h0 * 3600.0
            while t < h1 * 3600.0:
                sxml = "".join(
                    f'<stop busStop="bs_{s}" duration="{max(3.0, dwell[s]*rng.lognormal(0,0.25)):.1f}"/>'
                    for s in stops)
                vehicles.append((t, f"{per}.{i}", vtype, sxml))
                t += H * 60
                i += 1

    vehicles.sort(key=lambda v: v[0])      # SUMO requires departure-sorted routes
    R = ["<routes>",
         '  <vType id="bus12" vClass="bus" length="12" personCapacity="90" accel="1.0" decel="3.0"/>',
         '  <vType id="bus18" vClass="bus" length="18" personCapacity="150" accel="1.0" decel="3.0"/>',
         f'  <route id="out" edges="{" ".join(edges)}"/>']
    for dep, vid, vt, sxml in vehicles:
        R.append(f'  <vehicle id="{vid}" type="{vt}" route="out" depart="{dep:.1f}">{sxml}</vehicle>')
    R.append("</routes>")
    (Path(wd) / fname).write_text("\n".join(R))
    return len(vehicles)
