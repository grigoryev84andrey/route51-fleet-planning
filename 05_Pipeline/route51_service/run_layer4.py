"""Layer 4 — SUMO microsimulation: validate the Layer-3 plan vs baseline."""
from __future__ import annotations

import sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import runctx  # noqa: E402
from layer1.reference import load_reference  # noqa: E402
from layer4.network import build_network  # noqa: E402
from layer4.scenario import period_demand, build_routes, PERIOD_HOURS  # noqa: E402
from layer4.simulate import write_cfg, run_sumo, parse  # noqa: E402

END_S = 92000


def _simulate(plan, demand, edges, stops, sp, wd, tag):
    n = build_routes(plan, demand, edges, stops, sp, wd, f"{tag}.rou.xml")
    write_cfg(wd, f"{tag}.rou.xml", f"{tag}.sumocfg", END_S)
    run_sumo(wd, f"{tag}.sumocfg")
    tt, cv_up, cv_dn, _ = parse(wd, stops)
    return n, tt, cv_up, cv_dn


def main() -> None:
    t0 = time.time()
    ctx = runctx.start(__file__, inputs=["fused_demand_matrix.csv", "headway_schedule.csv"])
    cfg = ctx.cfg
    ref = load_reference(cfg)
    op, sp = cfg["operations"], cfg["simulation"]
    stops = sorted(ref.stops.keys())
    out = Path(cfg["paths"]["output_dir_abs"])
    wd = out / "sumo"

    fused = pd.read_csv(out / "fused_demand_matrix.csv")
    fused["demand"] = fused["demand"] * op["demand_scale"]
    demand = period_demand(fused, stops)

    # Plans: baseline (config) and optimized (Layer-3 headway_schedule).
    base_h = op["baseline_headways"]
    baseline = {p: (base_h[p], 0.0) for p in PERIOD_HOURS}
    sched = pd.read_csv(out / "headway_schedule.csv").set_index("period")
    optimized = {p: (float(sched.loc[p, "opt_headway_min"]), float(sched.loc[p, "opt_share_18m"]))
                 for p in PERIOD_HOURS}

    print("=" * 76)
    print("LAYER 4 — SUMO microsimulation (validate Layer-3 plan)")
    print("=" * 76)
    print(f"Demand scenario x{op['demand_scale']} | link speed {sp['link_speed_kmh']} km/h "
          f"(free-flow, NO background traffic)")

    net, edges = build_network(stops, ref.stops, sp["link_speed_kmh"], wd)
    print(f"Network built: {len(stops)} stops, {len(edges)} edges")

    rows = []
    for tag, plan in [("baseline", baseline), ("optimized", optimized)]:
        n, tt, cv_up, cv_dn = _simulate(plan, demand, edges, stops, sp, wd, tag)
        ow = tt["tt_s"].mean() / 60.0
        run = (tt["tt_s"] - tt["stop_s"]).mean() / 60.0
        dwell = tt["stop_s"].mean() / 60.0
        cycle = 2 * ow + 2 * sp["layover_min"]
        rows.append({"plan": tag, "buses": n, "one_way_min": round(ow, 1),
                     "running_min": round(run, 1), "dwell_min": round(dwell, 1),
                     "cycle_min": round(cycle, 1),
                     "headway_cv_upstream": round(cv_up, 3), "headway_cv_downstream": round(cv_dn, 3)})
        print("-" * 76)
        print(f"[{tag}] buses={n}  one-way={ow:.1f}min (running {run:.1f} + dwell {dwell:.1f})  "
              f"cycle≈{cycle:.1f}min")
        print(f"         headway irregularity (CV): upstream {cv_up:.3f} -> downstream {cv_dn:.3f}")

    df = pd.DataFrame(rows)
    df.to_csv(out / "sumo_results.csv", index=False)

    # --- Optimism bias + feedback to Layer 3 ---
    assumed_ow = sp["assumed_one_way_min"]
    assumed_cycle = 2 * assumed_ow + 2 * sp["layover_min"]
    b = df[df.plan == "baseline"].iloc[0]
    o = df[df.plan == "optimized"].iloc[0]
    print("-" * 76)
    print("Optimism-bias check & feedback to Layer 3:")
    print(f"   assumed one-way (Layer 3)        : {assumed_ow} min  (cycle {assumed_cycle} min)")
    print(f"   SUMO baseline one-way            : {b.one_way_min} min  (cycle {b.cycle_min} min)")
    print(f"     -> running time is free-flow (no traffic/signals): LOWER bound; "
          f"real running would be higher")
    print(f"     -> peak dwell at x{op['demand_scale']} load inflates time endogenously")
    fleet = op["fleet"]
    need_base = b.cycle_min / b.one_way_min  # placeholder; real fleet need = cycle/headway
    print(f"   optimized vs baseline one-way    : {o.one_way_min} vs {b.one_way_min} min  "
          f"(18 m faster boarding effect: {b.one_way_min - o.one_way_min:+.1f} min)")
    print(f"   headway regularity (downstream CV): optimized {o.headway_cv_downstream} vs "
          f"baseline {b.headway_cv_downstream}")
    if b.cycle_min > assumed_cycle:
        print(f"   FLAG: SUMO cycle {b.cycle_min} min > assumed {assumed_cycle} min -> Layer-3 fleet/"
              f"headway feasibility is OPTIMISTIC; peak may need more buses than {fleet}.")
    print("-" * 76)
    print(f"Written: sumo_results.csv  (+ SUMO scenario files in output/sumo/)")
    print(f"Elapsed: {time.time()-t0:.1f}s")
    print("=" * 76)


if __name__ == "__main__":
    main()
