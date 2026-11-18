"""Generate the Route-51 bus GPS feed and print a verification summary."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from config import load_config  # noqa: E402
from feed_gps import generate  # noqa: E402


def main() -> None:
    cfg = load_config(ROOT / "config.yaml")
    df, diag = generate(cfg)
    out = Path(cfg.run.output_dir)

    csv_path = out / "bus_gps_raw.csv"
    pq_path = out / "bus_gps_raw.parquet"
    df.to_csv(csv_path, index=False)
    try:
        df.to_parquet(pq_path, index=False)
    except Exception as exc:  # pragma: no cover
        pq_path = None
        print(f"(parquet skipped: {exc})")

    g = cfg["bus_gps"]
    print("=" * 68)
    print("ROUTE-51 BUS GPS FEED — verification")
    print("=" * 68)
    print(f"Day type / fleet        : {g['day_type']} / {diag['fleet']} buses")
    print(f"Service / departures    : {g['service_start']}-{g['service_end']}  "
          f"first {g['first_departure_weekday']} last {g['last_departure_weekday']}")
    print(f"Headway peak / off-peak : {g['headway_peak_min']} (uses 9) / "
          f"{g['headway_offpeak_min']} min")
    print("-" * 68)
    print(f"Departures generated    : {diag['n_departures']}  (both terminals)")
    print(f"Trips served            : {diag['n_trips']}   dropped (no bus): {diag['n_dropped']}")
    print(f"Vehicles actually used  : {diag['vehicles_used']} / {diag['fleet']}")
    print(f"Schedule adherence      : mean delay {diag['mean_delay_s']:.0f}s, "
          f"max delay {diag['max_delay_s']:.0f}s  "
          f"({'feasible' if diag['max_delay_s'] < 180 else 'TIGHT — headway/fleet strained'})")
    print("-" * 68)
    print(f"One-way duration (calc) : out {diag['one_way_out_min']} min / "
          f"in {diag['one_way_in_min']} min   (target {g['one_way_min']})")
    print(f"Polyline length         : {diag['polyline_km_out']} km  "
          f"(official {g['route_km']} km; gap = circuity)")
    print(f"GPS ping rows           : {diag['ping_rows']}  @ {g['ping_interval_s']}s")

    # Sanity: speed + occupancy ranges, dwell presence.
    print("-" * 68)
    print(f"Speed km/h  min/mean/max: {df.speed_kmh.min():.1f} / "
          f"{df.speed_kmh.mean():.1f} / {df.speed_kmh.max():.1f}")
    print(f"  rows at 0 km/h (dwell): {(df.speed_kmh == 0).mean()*100:.1f}%")
    print(f"Occupancy   min/mean/max: {df.occupancy.min()} / "
          f"{df.occupancy.mean():.0f} / {df.occupancy.max()}  (PLACEHOLDER, not demand-linked)")
    print(f"Crowding>0.8 share      : {(df.crowding_index > 0.8).mean()*100:.1f}%")
    vt = df.drop_duplicates('vehicle_id').vehicle_type.value_counts().to_dict()
    print(f"Vehicle-type mix        : {vt}")
    print("-" * 68)
    print("Sample (one vehicle, first pings):")
    one = df[df.vehicle_id == df.vehicle_id.iloc[0]].head(6)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(one[["vehicle_id", "trip_id", "timestamp", "lat", "lon",
                   "speed_kmh", "occupancy", "direction"]].to_string(index=False))
    print("-" * 68)
    print(f"Written: {csv_path}" + (f"  +  {pq_path}" if pq_path else ""))
    print("=" * 68)


if __name__ == "__main__":
    main()
