"""Stage 1-2: build the synthetic population + activities -> ground-truth OD."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from config import load_config  # noqa: E402
from geo import build_corridor  # noqa: E402
from population import synth_population  # noqa: E402
from activities import build_destinations, assign_activities  # noqa: E402


def main() -> None:
    t0 = time.time()
    cfg = load_config(ROOT / "config.yaml")
    rng = np.random.default_rng(cfg.run.random_seed)
    sa = cfg.study_area
    corr = build_corridor(
        stops_csv=f"{cfg.run.data_dir}/{sa.stops_csv}", res=sa.h3_resolution,
        buffer_m=sa.catchment_buffer_m, anchors=cfg["attraction_anchors"],
        cordon_zones=sa["cordon_zones"], density_model=cfg["population"]["home_density_model"],
    )
    out = Path(cfg.run.output_dir)
    grid = corr.internal_cells

    pop = synth_population(cfg, corr, rng)
    dests = build_destinations(cfg, corr)
    trips = assign_activities(pop, dests, corr, cfg, rng)

    # Ground-truth OD at cell level (grid cells kept; everything else -> EXTERNAL).
    def bucket(h):
        return h if h in grid else "EXTERNAL"
    od = trips.copy()
    od["o"] = od["o_h3"].map(bucket)
    od["d"] = od["d_h3"].map(bucket)
    gt_cells = (od.groupby(["o", "d", "hour"]).size()
                .reset_index(name="trips").sort_values("trips", ascending=False))

    # Ground-truth bus OD at stop level (the oracle for Layer 2 / ATTO).
    bus = trips[trips.route51].copy()
    gt_bus = (bus.groupby(["board_stop", "alight_stop", "hour"]).size()
              .reset_index(name="riders").sort_values("riders", ascending=False))

    pop.to_parquet(out / "persons.parquet", index=False)
    trips.to_parquet(out / "trips.parquet", index=False)
    gt_cells.to_csv(out / "ground_truth_od_cells.csv", index=False)
    gt_bus.to_csv(out / "ground_truth_bus_od.csv", index=False)

    # ----------------------------- report -----------------------------
    n = len(pop)
    print("=" * 70)
    print("STAGE 1-2 — population + activities -> ground-truth OD")
    print("=" * 70)
    print(f"Population (full catchment): {n:,}")
    print(f"  phone owners            : {pop.phone_owner.mean()*100:.1f}%  "
          f"(observed-operator '{cfg['telecom']['observed_operator']}' subset: "
          f"{((pop.operator==cfg['telecom']['observed_operator']).mean())*100:.1f}% of all)")
    print(f"  operator mix            : "
          f"{ {k: round(v,3) for k,v in pop.operator.value_counts(normalize=True).round(3).items()} }")
    print(f"  primary purpose         : "
          f"{ {k: round(v,3) for k,v in pop.primary_purpose.value_counts(normalize=True).items()} }")
    print("-" * 70)
    print(f"Trips total               : {len(trips):,}  "
          f"({len(trips)/n:.2f} per person)")
    print(f"  by purpose              : {dict(trips.purpose.value_counts())}")
    print(f"  bus (Route-51) trips    : {int(trips.route51.sum()):,}  "
          f"({trips.route51.mean()*100:.1f}% of all trips)")
    riders = bus.person_id.nunique()
    print(f"  distinct bus riders     : {riders:,}  ({riders/n*100:.1f}% of pop)")
    print("-" * 70)
    print(f"Ground-truth cell OD rows : {len(gt_cells):,}  "
          f"(total trips {gt_cells.trips.sum():,})")
    print(f"Ground-truth bus OD rows  : {len(gt_bus):,}  "
          f"(total riders {gt_bus.riders.sum():,})  [oracle, UNsuppressed]")
    print("Top boarding stops (riders):")
    top_board = bus.board_stop.value_counts().head(5)
    for sid, c in top_board.items():
        nm = next((s.name for s in corr.stops if s.id == sid), "?")
        print(f"   {sid}  {nm[:34]:<34} {c:>6}")
    print("Top alighting stops (riders):")
    for sid, c in bus.alight_stop.value_counts().head(5).items():
        nm = next((s.name for s in corr.stops if s.id == sid), "?")
        print(f"   {sid}  {nm[:34]:<34} {c:>6}")
    print("-" * 70)
    print(f"Written: persons.parquet, trips.parquet, ground_truth_od_cells.csv, "
          f"ground_truth_bus_od.csv")
    print(f"Elapsed: {time.time()-t0:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
