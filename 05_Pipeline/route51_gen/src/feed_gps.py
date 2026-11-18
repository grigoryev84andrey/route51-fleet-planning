"""Feed 3 — Bus GPS telemetry for Route 51 (timetable-driven, TSTX-telemetry proxy).

Generates per-vehicle position / speed / occupancy traces by:
  1. building a time-varying headway profile (peak vs off-peak),
  2. generating origin departures from BOTH terminals across the service day,
  3. assigning departures to a finite fleet by earliest-free-vehicle (honouring
     one-way time + terminal layover), reporting feasibility diagnostics,
  4. simulating each trip along the directional stop polyline with a cruise+dwell
     speed profile calibrated to hit the official one-way duration EXACTLY,
  5. sampling every `ping_interval_s`, attaching a PLACEHOLDER occupancy profile,
  6. applying the GPS degradation block (jitter, dropout, clock jitter).

SUMO is the downstream evaluation engine only; this feed is a self-contained
data source that real TSTX GPS can replace without code change.

NOTE on occupancy: a placeholder load curve (time-of-day x position-along-route),
NOT demand-linked. It is replaced by demand-derived boardings once Stages 1-2
(population + activities) exist. Flagged in output and README.

NOTE on the 41-min anchor: because the trace is calibrated to the official
schedule duration (which embeds real congestion), this feed is a realistic
calibration target — unlike free-flow SUMO, which under-states cycle time
(the ~12-min gap). Keep that distinction: SUMO = eval, this = observed-ops proxy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from geo import Stop, haversine_m, load_stops

_M_PER_DEG_LAT = 111_320.0


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #
def hhmm_to_sec(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 3600 + int(m) * 60


def _in_windows(sec: int, windows: list[list[str]]) -> bool:
    for w in windows:
        if hhmm_to_sec(w[0]) <= sec < hhmm_to_sec(w[1]):
            return True
    return False


# --------------------------------------------------------------------------- #
# Directional stop sequence + trip timeline
# --------------------------------------------------------------------------- #
def directional_stops(stops: list[Stop], outbound: bool, canonical_outbound: str) -> list[Stop]:
    """Return the ordered stop list for a trip.

    CSV is S01..S23 (Beshagach..Yunusabad). Canonical outbound S23_to_S01 means
    Yunusabad -> Beshagach = CSV reversed. Inbound = CSV forward.
    """
    rev_for_outbound = canonical_outbound == "S23_to_S01"
    if outbound:
        return list(reversed(stops)) if rev_for_outbound else list(stops)
    return list(stops) if rev_for_outbound else list(reversed(stops))


@dataclass
class TripTimeline:
    """Piecewise position/speed timeline for one one-way trip (t in seconds from departure)."""
    seq: list[Stop]
    segments: list[tuple]  # (kind, t0, t1, idx, speed_mps)
    duration_s: float
    length_m: float

    def sample(self, t: float) -> tuple[float, float, float]:
        """Return (lat, lon, speed_kmh) at trip-time t."""
        for kind, t0, t1, idx, spd in self.segments:
            if t0 <= t <= t1:
                if kind == "dwell":
                    s = self.seq[idx]
                    return s.lat, s.lon, 0.0
                a, b = self.seq[idx], self.seq[idx + 1]
                f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
                return (a.lat + (b.lat - a.lat) * f,
                        a.lon + (b.lon - a.lon) * f,
                        spd * 3.6)
        last = self.seq[-1]
        return last.lat, last.lon, 0.0

    def fraction(self, t: float) -> float:
        return min(1.0, max(0.0, t / self.duration_s))


def build_timeline(seq: list[Stop], one_way_s: float, dwell_s: float) -> TripTimeline:
    """Cruise+dwell timeline calibrated so total == one_way_s exactly."""
    seg_len = [haversine_m(a.lat, a.lon, b.lat, b.lon) for a, b in zip(seq[:-1], seq[1:])]
    total_len = sum(seg_len) or 1.0
    n_dwell = max(0, len(seq) - 2)            # intermediate stops only
    cruise_total = max(1.0, one_way_s - n_dwell * dwell_s)

    segs: list[tuple] = []
    t = 0.0
    for k, ln in enumerate(seg_len):
        seg_t = cruise_total * ln / total_len
        spd = ln / seg_t if seg_t > 0 else 0.0
        segs.append(("cruise", t, t + seg_t, k, spd))
        t += seg_t
        if k + 1 < len(seq) - 1:               # arrived at an intermediate stop -> dwell
            segs.append(("dwell", t, t + dwell_s, k + 1, 0.0))
            t += dwell_s
    return TripTimeline(seq=seq, segments=segs, duration_s=t, length_m=total_len)


# --------------------------------------------------------------------------- #
# Schedule (headway -> departures -> vehicle assignment)
# --------------------------------------------------------------------------- #
def headway_sec_at(sec: int, cfg) -> int:
    if _in_windows(sec, cfg["peak_windows"]):
        lo, hi = cfg["headway_peak_min"]
        return int(round((lo + hi) / 2 * 60))
    return int(cfg["headway_offpeak_min"] * 60)


def generate_departures(cfg) -> list[dict]:
    """Departures from both terminals across the service day."""
    first = hhmm_to_sec(cfg["first_departure_weekday"])
    last = hhmm_to_sec(cfg["last_departure_weekday"])
    deps: list[dict] = []
    # Terminal A = outbound origin (Yunusabad/S23 end); Terminal B = Beshagach/S01 end.
    # Offset B by half a headway for even corridor spacing.
    for terminal, offset_frac in (("A", 0.0), ("B", 0.5)):
        t = first
        first_h = headway_sec_at(t, cfg)
        t = first + int(first_h * offset_frac)
        while t <= last:
            deps.append({"t": t, "terminal": terminal, "outbound": terminal == "A"})
            t += headway_sec_at(t, cfg)
    deps.sort(key=lambda d: d["t"])
    return deps


def assign_vehicles(deps: list[dict], fleet: int, one_way_s: float, layover_s: float) -> dict:
    """Earliest-free-vehicle assignment. Returns trips + feasibility diagnostics."""
    # Seed fleet split across the two terminals, all free at service start of first dep.
    half = fleet // 2
    vehicles = []
    for i in range(fleet):
        vehicles.append({"id": f"BUS_{i+1:02d}", "free_at": -1e9,
                         "at": "A" if i < half else "B"})
    trips, delays, dropped = [], [], 0
    for d in deps:
        pool = [v for v in vehicles if v["at"] == d["terminal"]]
        if not pool:
            dropped += 1
            continue
        v = min(pool, key=lambda x: x["free_at"])
        actual = max(d["t"], v["free_at"])
        delays.append(actual - d["t"])
        other = "B" if d["terminal"] == "A" else "A"
        v["free_at"] = actual + one_way_s + layover_s
        v["at"] = other
        trips.append({"vehicle_id": v["id"], "depart_s": actual,
                      "outbound": d["outbound"], "sched_s": d["t"]})
    diag = {
        "n_departures": len(deps),
        "n_trips": len(trips),
        "n_dropped": dropped,
        "vehicles_used": len({t["vehicle_id"] for t in trips}),
        "max_delay_s": max(delays) if delays else 0,
        "mean_delay_s": float(np.mean(delays)) if delays else 0.0,
    }
    return {"trips": trips, "diag": diag}


# --------------------------------------------------------------------------- #
# Occupancy (PLACEHOLDER) + degradation
# --------------------------------------------------------------------------- #
def occupancy_placeholder(sec: int, frac: float, outbound: bool, capacity: int,
                          peak_windows) -> int:
    """Time-of-day load factor x along-route hump, skewed by direction. PLACEHOLDER."""
    if _in_windows(sec, peak_windows):
        lf = 0.85
    elif (hhmm_to_sec("06:00") <= sec < hhmm_to_sec("07:00")
          or hhmm_to_sec("10:00") <= sec < hhmm_to_sec("16:00")):
        lf = 0.50
    else:
        lf = 0.25
    hump = math.sin(math.pi * frac) ** 0.8          # 0 at terminals, max mid-route
    # Outbound = toward centre (Beshagach): fuller approaching centre (late frac).
    dir_w = (0.5 + 0.5 * frac) if outbound else (1.0 - 0.5 * frac)
    return int(round(capacity * lf * hump * dir_w))


def _assign_vehicle_types(trips, vtypes, rng) -> dict:
    ids = sorted({t["vehicle_id"] for t in trips})
    names = list(vtypes.keys())
    probs = np.array([vtypes[n]["share"] for n in names], float)
    probs /= probs.sum()
    choice = rng.choice(len(names), size=len(ids), p=probs)
    return {vid: names[choice[i]] for i, vid in enumerate(ids)}


def generate(cfg_root) -> tuple[pd.DataFrame, dict]:
    """Generate the full GPS feed. Returns (dataframe, diagnostics)."""
    g = cfg_root["bus_gps"]
    rng = np.random.default_rng(cfg_root["run"]["random_seed"])
    stops = load_stops(f"{cfg_root['run']['data_dir']}/{cfg_root['study_area']['stops_csv']}",
                       cfg_root["study_area"]["h3_resolution"])

    one_way_s = g["one_way_min"] * 60.0
    layover_s = g["layover_min"] * 60.0
    dwell_s = g["dwell_s"]
    ping = g["ping_interval_s"]
    canon = g["direction_outbound"]

    # Pre-build the two directional timelines.
    tl_out = build_timeline(directional_stops(stops, True, canon), one_way_s, dwell_s)
    tl_in = build_timeline(directional_stops(stops, False, canon), one_way_s, dwell_s)

    fleet = g["fleet_weekday"] if g["day_type"] == "weekday" else g["fleet_weekend"]
    deps = generate_departures(g)
    sched = assign_vehicles(deps, fleet, one_way_s, layover_s)
    trips, diag = sched["trips"], sched["diag"]

    vtype = _assign_vehicle_types(trips, g["vehicle_types"], rng)
    base = pd.Timestamp(g["base_date"])

    deg = cfg_root["degradation"]["gps"]
    jit_m, p_drop, clk = deg["gps_jitter_m"], deg["p_gps_dropout"], deg["clock_jitter_s"]

    rows = []
    for ti, tr in enumerate(trips):
        tl = tl_out if tr["outbound"] else tl_in
        cap = g["vehicle_types"][vtype[tr["vehicle_id"]]]["capacity"]
        n = int(tl.duration_s // ping)
        for k in range(n + 1):
            tt = k * ping
            if p_drop and rng.random() < p_drop:        # missing ping
                continue
            lat, lon, spd = tl.sample(tt)
            abs_s = tr["depart_s"] + tt
            frac = tl.fraction(tt)
            occ = occupancy_placeholder(int(abs_s % 86400), frac, tr["outbound"],
                                        cap, g["peak_windows"])
            if jit_m:                                    # position noise
                lat += rng.normal(0, jit_m / _M_PER_DEG_LAT)
                lon += rng.normal(0, jit_m / (_M_PER_DEG_LAT * math.cos(math.radians(lat))))
            ts_s = abs_s + (rng.normal(0, clk) if clk else 0.0)
            rows.append({
                "vehicle_id": tr["vehicle_id"],
                "trip_id": f"T{ti:04d}",
                "timestamp": base + pd.Timedelta(seconds=ts_s),
                "epoch_s": round(float(ts_s), 1),
                "lat": round(float(lat), 6),
                "lon": round(float(lon), 6),
                "speed_kmh": round(float(spd), 1),
                "occupancy": int(occ),                  # PLACEHOLDER (not demand-linked)
                "crowding_index": round(occ / cap, 3),
                "direction": "outbound" if tr["outbound"] else "inbound",
                "route_id": "51",
                "vehicle_type": vtype[tr["vehicle_id"]],
            })
    df = pd.DataFrame(rows).sort_values(["vehicle_id", "epoch_s"]).reset_index(drop=True)
    diag.update({
        "one_way_out_min": round(tl_out.duration_s / 60, 2),
        "one_way_in_min": round(tl_in.duration_s / 60, 2),
        "polyline_km_out": round(tl_out.length_m / 1000, 2),
        "ping_rows": len(df),
        "fleet": fleet,
    })
    return df, diag
