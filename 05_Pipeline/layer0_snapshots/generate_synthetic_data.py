"""
Synthetic Data Generator for Tashkent Bus Route Optimization Prototype
======================================================================
Produces sample CSVs for all three data streams + static reference data:
  1. telecom_od_matrix.csv      — Hourly OD flows between H3 hexagons
  2. atto_boarding_events.csv   — Tap-in fare card transactions (no alighting)
  3. bus_gps_telemetry.csv      — 30-second vehicle position pings
  4. route_stops.csv            — Illustrative stop list (12 points, schema demo)
  5. fleet_catalog.csv          — Vehicle types and capacities
  6. gtfs_baseline_headways.csv — March 2024 baseline timetable

SCOPE. This script demonstrates the Layer-0 data contracts. It produced no
result in the thesis: Chapter 3 comes entirely from 3_Pipeline_and_Results/,
whose stop catalogue is route51_gen/data/Busstations.csv — 23 stops, not the
12 emitted here. Only the headway set is shared with the thesis.

Two things this output must not be mistaken for real data. The stop
coordinates are Tashkent locations but describe a different, shorter
representation of Line 51 than the thesis models. And the h3_level9 /
h3_level7 columns are produced by lat_lon_to_pseudo_h3() below, which
fabricates identifier-shaped strings to show the column layout — they are not
valid H3 cells and the h3 library rejects them. Real indexing uses
h3.latlng_to_cell() in route51_gen/src/ and route51_service/layer1/.

Demand patterns follow realistic weekday peak/off-peak distributions.
"""

import csv
import hashlib
import os
import random
from datetime import datetime, timedelta

random.seed(42)
OUTPUT_DIR = "/home/claude/synthetic_data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================================
# STATIC REFERENCE DATA: Bus stops for Route 51
# Coordinates are approximate real locations along these Tashkent corridors
# ============================================================================

ROUTE_51_STOPS = [
    # Route 51: Shota Rustaveli Street corridor (roughly SW to NE)
    {"stop_id": "R51_01", "name": "Yunusobod bozori",        "lat": 41.3415, "lon": 69.2867, "seq": 1},
    {"stop_id": "R51_02", "name": "Bodomzor",                "lat": 41.3380, "lon": 69.2790, "seq": 2},
    {"stop_id": "R51_03", "name": "Minor",                   "lat": 41.3340, "lon": 69.2715, "seq": 3},
    {"stop_id": "R51_04", "name": "Abdulla Qodiriy",         "lat": 41.3280, "lon": 69.2680, "seq": 4},
    {"stop_id": "R51_05", "name": "Shota Rustaveli",         "lat": 41.3220, "lon": 69.2640, "seq": 5},
    {"stop_id": "R51_06", "name": "Pakhtakor",               "lat": 41.3160, "lon": 69.2610, "seq": 6},
    {"stop_id": "R51_07", "name": "Oybek",                   "lat": 41.3110, "lon": 69.2570, "seq": 7},
    {"stop_id": "R51_08", "name": "Navoi",                   "lat": 41.3070, "lon": 69.2530, "seq": 8},
    {"stop_id": "R51_09", "name": "Amir Temur xiyoboni",     "lat": 41.3110, "lon": 69.2790, "seq": 9},
    {"stop_id": "R51_10", "name": "Toshkent vokzali",        "lat": 41.2990, "lon": 69.2780, "seq": 10},
    {"stop_id": "R51_11", "name": "Sergeli yo'nalishi",      "lat": 41.2880, "lon": 69.2750, "seq": 11},
    {"stop_id": "R51_12", "name": "Olmazor",                 "lat": 41.2810, "lon": 69.2700, "seq": 12},
]

ALL_STOPS = {s["stop_id"]: s for s in ROUTE_51_STOPS}

# Fleet catalog
FLEET = [
    {"vehicle_id": "BUS_51_001", "route_id": "51",  "type": "standard_12m", "capacity": 85,  "seated": 35},
    {"vehicle_id": "BUS_51_002", "route_id": "51",  "type": "standard_12m", "capacity": 85,  "seated": 35},
    {"vehicle_id": "BUS_51_003", "route_id": "51",  "type": "midi_8m",      "capacity": 50,  "seated": 25},
    {"vehicle_id": "BUS_51_004", "route_id": "51",  "type": "standard_12m", "capacity": 85,  "seated": 35},
    {"vehicle_id": "BUS_51_005", "route_id": "51",  "type": "midi_8m",      "capacity": 50,  "seated": 25},
]

# Baseline headways (March 2024 GTFS static timetable)
BASELINE_HEADWAYS = [
    {"route_id": "51",  "period": "morning_peak",  "start": "07:00", "end": "09:00", "headway_min": 8},
    {"route_id": "51",  "period": "midday",         "start": "09:00", "end": "16:00", "headway_min": 15},
    {"route_id": "51",  "period": "evening_peak",   "start": "16:00", "end": "19:00", "headway_min": 8},
    {"route_id": "51",  "period": "evening",         "start": "19:00", "end": "23:00", "headway_min": 20},
    {"route_id": "51",  "period": "early_morning",   "start": "05:30", "end": "07:00", "headway_min": 20},
]


# ============================================================================
# HELPER: Pseudo-H3 index generator
# ============================================================================

def lat_lon_to_pseudo_h3(lat, lon, resolution=9):
    """
    Generate a deterministic pseudo-H3 index string from lat/lon.
    Real H3 requires the C library; this produces realistic-looking
    hex IDs that are consistent for the same input coordinates.
    Format mirrors real H3: '89' prefix for res 9, '87' for res 7.
    """
    prefix = {7: "87", 9: "89"}[resolution]
    # Quantize to resolution grid
    scale = {7: 100, 9: 1000}[resolution]
    qlat = int(lat * scale)
    qlon = int(lon * scale)
    raw = hashlib.md5(f"{qlat},{qlon}".encode()).hexdigest()[:13]
    return f"{prefix}{raw}"


# ============================================================================
# DEMAND PROFILE: Realistic hourly passenger multipliers
# ============================================================================

def get_hourly_demand_multiplier(hour):
    """
    Returns a demand multiplier (0.0-1.0) for a given hour.
    Models typical Tashkent weekday pattern:
      - Morning peak: 07-09 (1.0)
      - Midday: 09-16 (0.4-0.5)
      - Evening peak: 16-19 (0.9)
      - Evening: 19-23 (0.25)
      - Night: 23-05 (0.0)
      - Early morning: 05-07 (0.15)
    """
    profile = {
        0: 0.0,  1: 0.0,  2: 0.0,  3: 0.0,  4: 0.0,
        5: 0.10, 6: 0.25,
        7: 0.85, 8: 1.00,
        9: 0.50, 10: 0.40, 11: 0.40, 12: 0.45,
        13: 0.40, 14: 0.40, 15: 0.45,
        16: 0.80, 17: 0.90, 18: 0.85,
        19: 0.40, 20: 0.30, 21: 0.20, 22: 0.15, 23: 0.05,
    }
    return profile.get(hour, 0.0)


def get_stop_weight(stop_id):
    """
    Relative boarding probability per stop.
    Hub/central stops get higher weight.
    """
    hub_stops = {"R51_06", "R51_07", "R51_08", "R51_09"}
    terminal_stops = {"R51_01", "R51_12"}
    if stop_id in hub_stops:
        return 3.0
    elif stop_id in terminal_stops:
        return 2.0
    else:
        return 1.0


# ============================================================================
# GENERATOR 1: Telecom OD Matrix
# ============================================================================

def generate_telecom_od_matrix(sim_date, hours=24):
    """
    Produces hourly OD flows between H3 Level 9 hexagons.
    Each row = one origin-destination pair for one hour.
    Population_Count reflects ALL movement (not just bus riders).
    """
    rows = []
    # Build hex index for each stop area (represents the cell tower coverage zone)
    stop_hexes = {}
    for s in ROUTE_51_STOPS:
        h9 = lat_lon_to_pseudo_h3(s["lat"], s["lon"], 9)
        h7 = lat_lon_to_pseudo_h3(s["lat"], s["lon"], 7)
        stop_hexes[s["stop_id"]] = {"h3_level9": h9, "h3_level7": h7,
                                     "lat": s["lat"], "lon": s["lon"]}

    hex_list = list(set(v["h3_level9"] for v in stop_hexes.values()))

    for hour in range(hours):
        ts = sim_date.replace(hour=hour, minute=0, second=0)
        dm = get_hourly_demand_multiplier(hour)
        if dm == 0:
            continue

        # Generate OD pairs between hexagons
        for i, origin_hex in enumerate(hex_list):
            for j, dest_hex in enumerate(hex_list):
                if i == j:
                    continue
                # Base flow = distance-decay + demand multiplier
                base_flow = random.randint(20, 200)
                flow = int(base_flow * dm * random.uniform(0.5, 1.5))
                if flow < 5:
                    continue

                rows.append({
                    "timestamp": ts.isoformat(),
                    "origin_h3_level9": origin_hex,
                    "destination_h3_level9": dest_hex,
                    "population_count": flow,
                    "aggregation_period_hours": 1,
                    "data_source": "telecom_proxy",
                })

    return rows


# ============================================================================
# GENERATOR 2: ATTO Boarding Events
# ============================================================================

TARIFF_TYPES = ["standard", "student", "pensioner", "social"]
TARIFF_WEIGHTS = [0.55, 0.20, 0.15, 0.10]

def generate_atto_boarding_events(sim_date, target_daily_boardings=2300):
    """
    Produces tap-in boarding events across all stops.

    NOTE — the target was 4500 when the catalogue held both route 51 and route 131,
    and it is a CITY-WIDE total spread over whatever stops exist. Route 131 has since
    been removed (the study is Line 51 only), so leaving 4500 would have concentrated
    the whole two-route demand onto one route and doubled Line 51's boardings. The
    target is therefore halved to preserve Line 51's original scale; the shipped
    atto_boarding_events.csv is the route-51 subset of the original two-route run.
    NO alighting stop is recorded (alighting blindness problem).
    Card IDs are hashed for privacy simulation.
    """
    rows = []
    all_stops = ROUTE_51_STOPS

    # Distribute boardings across hours based on demand profile
    hourly_budget = {}
    total_weight = sum(get_hourly_demand_multiplier(h) for h in range(24))
    for h in range(24):
        dm = get_hourly_demand_multiplier(h)
        hourly_budget[h] = int(target_daily_boardings * dm / total_weight)

    card_counter = 0
    for hour in range(24):
        n_boardings = hourly_budget[hour]
        if n_boardings == 0:
            continue

        # Distribute across stops weighted by stop importance
        stop_weights = [get_stop_weight(s["stop_id"]) for s in all_stops]
        total_w = sum(stop_weights)
        stop_probs = [w / total_w for w in stop_weights]

        for _ in range(n_boardings):
            stop = random.choices(all_stops, weights=stop_probs, k=1)[0]
            route_id = "51"

            # Random minute within the hour
            minute = random.randint(0, 59)
            second = random.randint(0, 59)
            ts = sim_date.replace(hour=hour, minute=minute, second=second)

            # Generate deterministic card hash
            card_counter += 1
            raw_id = f"CARD_{card_counter:06d}_{sim_date.strftime('%Y%m%d')}"
            hashed_id = hashlib.sha256(raw_id.encode()).hexdigest()[:16]

            tariff = random.choices(TARIFF_TYPES, weights=TARIFF_WEIGHTS, k=1)[0]

            rows.append({
                "hashed_card_id": hashed_id,
                "timestamp": ts.isoformat(),
                "bus_stop_id": stop["stop_id"],
                "route_id": route_id,
                "tariff_type": tariff,
                # NOTE: no alighting stop -- this is the "blindness" by design
            })

    # Sort by timestamp
    rows.sort(key=lambda r: r["timestamp"])
    return rows


# ============================================================================
# GENERATOR 3: Bus GPS Telemetry
# ============================================================================

def generate_bus_gps_telemetry(sim_date, interval_sec=30):
    """
    Produces GPS pings every 30 seconds for each vehicle in the fleet.
    Vehicles operate from 05:30 to 23:00.
    Occupancy varies by time of day and position on route.
    """
    rows = []
    start_time = sim_date.replace(hour=5, minute=30, second=0)
    end_time = sim_date.replace(hour=23, minute=0, second=0)

    for vehicle in FLEET:
        route_id = vehicle["route_id"]
        stops = ROUTE_51_STOPS
        capacity = vehicle["capacity"]

        current_time = start_time
        stop_idx = 0
        direction = 1  # 1 = forward, -1 = reverse

        while current_time < end_time:
            stop = stops[stop_idx]
            hour = current_time.hour
            dm = get_hourly_demand_multiplier(hour)

            # Occupancy: base load * demand multiplier * stop centrality
            base_occupancy = capacity * 0.3
            stop_factor = get_stop_weight(stop["stop_id"]) / 3.0
            occupancy = int(min(
                base_occupancy + capacity * dm * stop_factor * random.uniform(0.3, 0.9),
                capacity
            ))
            occupancy = max(0, occupancy)

            # Speed: slower in center, faster at edges; slower in peak
            base_speed = random.uniform(15, 35)
            if stop["stop_id"] in {"R51_06", "R51_07"}:
                base_speed *= 0.5  # congested center
            if dm > 0.7:
                base_speed *= 0.7  # peak hour slowdown

            # Add small GPS jitter
            lat = stop["lat"] + random.uniform(-0.0005, 0.0005)
            lon = stop["lon"] + random.uniform(-0.0005, 0.0005)

            rows.append({
                "vehicle_id": vehicle["vehicle_id"],
                "timestamp": current_time.isoformat(),
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "speed_kmh": round(base_speed, 1),
                "current_occupancy": occupancy,
                "capacity": capacity,
                "route_id": route_id,
            })

            # Advance time and position
            current_time += timedelta(seconds=interval_sec)
            # Move between stops every ~3-5 minutes (6-10 pings)
            if random.random() < 0.15:
                stop_idx += direction
                if stop_idx >= len(stops):
                    direction = -1
                    stop_idx = len(stops) - 2
                elif stop_idx < 0:
                    direction = 1
                    stop_idx = 1

    rows.sort(key=lambda r: (r["vehicle_id"], r["timestamp"]))
    return rows


# ============================================================================
# WRITE CSV FILES
# ============================================================================

def write_csv(filename, rows, fieldnames=None):
    if not rows:
        print(f"  SKIP {filename} (no rows)")
        return
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {filename}: {len(rows)} rows")


def main():
    sim_date = datetime(2024, 3, 12)  # A Tuesday in March 2024 (baseline period)
    print(f"Generating synthetic data for {sim_date.strftime('%Y-%m-%d')} (Tuesday)")
    print(f"Output directory: {OUTPUT_DIR}\n")

    # --- Static reference data ---
    print("=== Static Reference Data ===")

    # 1. Route stops
    stop_rows = []
    for s in ROUTE_51_STOPS:
        s_copy = dict(s)
        s_copy["route_id"] = "51"
        s_copy["h3_level9"] = lat_lon_to_pseudo_h3(s["lat"], s["lon"], 9)
        s_copy["h3_level7"] = lat_lon_to_pseudo_h3(s["lat"], s["lon"], 7)
        stop_rows.append(s_copy)
    write_csv("route_stops.csv", stop_rows,
              ["stop_id", "name", "route_id", "seq", "lat", "lon", "h3_level9", "h3_level7"])

    # 2. Fleet catalog
    write_csv("fleet_catalog.csv", FLEET,
              ["vehicle_id", "route_id", "type", "capacity", "seated"])

    # 3. Baseline headways
    write_csv("gtfs_baseline_headways.csv", BASELINE_HEADWAYS,
              ["route_id", "period", "start", "end", "headway_min"])

    # --- Dynamic data streams ---
    print("\n=== Data Stream 1: Telecom OD Matrix ===")
    telecom_rows = generate_telecom_od_matrix(sim_date)
    write_csv("telecom_od_matrix.csv", telecom_rows,
              ["timestamp", "origin_h3_level9", "destination_h3_level9",
               "population_count", "aggregation_period_hours", "data_source"])

    print("\n=== Data Stream 2: ATTO Boarding Events ===")
    atto_rows = generate_atto_boarding_events(sim_date, target_daily_boardings=2300)
    write_csv("atto_boarding_events.csv", atto_rows,
              ["hashed_card_id", "timestamp", "bus_stop_id", "route_id", "tariff_type"])

    print("\n=== Data Stream 3: Bus GPS Telemetry ===")
    gps_rows = generate_bus_gps_telemetry(sim_date)
    write_csv("bus_gps_telemetry.csv", gps_rows,
              ["vehicle_id", "timestamp", "latitude", "longitude",
               "speed_kmh", "current_occupancy", "capacity", "route_id"])

    # --- Summary stats ---
    print("\n=== Summary ===")
    print(f"Total files generated: 6")
    print(f"Simulation date: {sim_date.strftime('%Y-%m-%d')} (Tuesday)")
    print(f"Route 51 stops: {len(ROUTE_51_STOPS)}")
    print(f"Fleet size: {len(FLEET)} vehicles")
    print(f"Telecom OD rows: {len(telecom_rows)}")
    print(f"ATTO boarding events: {len(atto_rows)}")
    print(f"GPS telemetry pings: {len(gps_rows)}")

    # Quick data quality checks
    print("\n=== Data Quality Checks ===")

    # ATTO: verify no alighting column
    atto_cols = set(atto_rows[0].keys())
    assert "alighting_stop" not in atto_cols, "FAIL: alighting column should NOT exist"
    print("PASS: ATTO has no alighting stop (blindness preserved)")

    # Telecom: verify H3 format
    sample_h3 = telecom_rows[0]["origin_h3_level9"]
    assert sample_h3.startswith("89"), f"FAIL: H3 L9 should start with '89', got {sample_h3[:2]}"
    print(f"PASS: H3 Level 9 format correct (sample: {sample_h3})")

    # GPS: verify occupancy <= capacity
    over_cap = [r for r in gps_rows if r["current_occupancy"] > r["capacity"]]
    assert len(over_cap) == 0, f"FAIL: {len(over_cap)} pings exceed capacity"
    print("PASS: No GPS pings exceed vehicle capacity")

    # Boarding distribution check
    peak_boardings = len([r for r in atto_rows if 7 <= int(r["timestamp"][11:13]) < 9])
    offpeak_boardings = len([r for r in atto_rows if 10 <= int(r["timestamp"][11:13]) < 14])
    ratio = peak_boardings / max(offpeak_boardings, 1)
    print(f"Peak/off-peak boarding ratio: {ratio:.2f} (expect ~2.0+)")


if __name__ == "__main__":
    main()
