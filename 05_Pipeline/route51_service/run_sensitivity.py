"""Layer-3 sensitivity series of protocol route51_v2 (plan S13), one factor at a time.

From the central profile (ramp, theta 0.80, lambda(1) 2.0, r = 1.5, demand x3) each
variant changes one factor, re-optimises with the protocol seeds and budget, and
selects by the protocol rule. Every selected plan is reported
  * on its own model (the variant's crowding definition), and
  * re-evaluated on the central model, so plans can be compared on one scale,
against the baseline timetable under the same model. The central settings are not
chosen from these results.

Outputs (run directory): sensitivity_layer3.csv, sensitivity_layer3.json
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import runctx  # noqa: E402
from layer3 import provenance  # noqa: E402
from layer3.dispatch import dispatch_metrics  # noqa: E402
from layer3.study import optimise_one_seed, select_from_candidates, setup  # noqa: E402

SEEDS = (42, 43, 44)

VARIANTS = [
    ("central", {}, None),
    ("theta_0.60", {"crowding": {"threshold": 0.60}, "operations": {"comfort_load_factor": 0.60}}, None),
    ("theta_0.90", {"crowding": {"threshold": 0.90}, "operations": {"comfort_load_factor": 0.90}}, None),
    ("form_step", {"crowding": {"model": "step"}}, None),
    ("form_none", {"crowding": {"model": "none"}}, None),
    ("lambda_2.32", {"crowding": {"multiplier_at_full_capacity": 2.32}}, None),
    ("r_1.3", {"operations": {"veh_cost_large": 1.3}}, None),
    ("r_1.7", {"operations": {"veh_cost_large": 1.7}}, None),
    ("demand_x1", {}, 1.0),
    ("demand_x2", {}, 2.0),
]


def main() -> None:
    def extra(ap):
        ap.add_argument("--workers", type=int, default=6)
    ctx = runctx.start(__file__, inputs=["fused_demand_matrix.csv"], add_args=extra)
    cfg, out = ctx.cfg, ctx.out
    fused = pd.read_csv(ctx.input("fused_demand_matrix.csv"))
    central = setup(cfg, fused)
    t0 = time.time()
    tasks = {}
    with ProcessPoolExecutor(max_workers=ctx.args.workers) as pool:
        for name, ov, scale in VARIANTS:
            for s in SEEDS:
                tasks[pool.submit(optimise_one_seed, (cfg, fused, scale, ov, s))] = name
        pools: dict = {}
        for i, f in enumerate(as_completed(tasks), 1):
            pools.setdefault(tasks[f], []).extend(f.result())
            print(f"  {i}/{len(tasks)} optimiser runs", flush=True)
    rows, detail = [], {}
    for name, ov, scale in VARIANTS:
        st = setup(cfg, fused, demand_scale=scale, cfg_overrides=ov)
        sel = select_from_candidates(st, pools[name])
        c = sel["candidate"]
        row = {"variant": name, "overrides": json.dumps(ov), "demand_scale": st.scale,
               "crowding_model": st.params["crowding"]["model"], "theta": st.params["crowding"]["theta"],
               "lambda_full": st.params["crowding"]["lambda_full"],
               "r": st.params["veh_cost_large"] / st.params["veh_cost_small"],
               "budget": st.budget, "pool_size": len(pools[name]), "examined": len(sel["log"])}
        if c is None:
            row.update({"feasible": False})
            rows.append(row)
            continue
        own, base_own = sel["dispatch"], st.base_disp
        # the same trips on the central model (same demand scale as the variant)
        cst = central if scale is None else setup(cfg, fused, demand_scale=scale)
        on_central = dispatch_metrics(sel["trips"], cst.summary, cst.params)
        base_central = cst.base_disp
        row.update({
            "feasible": True, "cid": c.cid,
            **{f"h_{p}": h for p, h in zip(("am", "md", "pm", "off"), c.headways)},
            **{f"f18_{p}": f for p, f in zip(("am", "md", "pm", "off"), c.shares)},
            "own_wait": own["wait"], "own_crowd_min": own["crowd_min"], "own_gen_cost": own["gen_cost"],
            "own_cost": own["cost"],
            "own_gen_cost_change_pct": 100 * (own["gen_cost"] - base_own["gen_cost"]) / base_own["gen_cost"],
            "central_crowd_min": on_central["crowd_min"], "central_gen_cost": on_central["gen_cost"],
            "central_gen_cost_change_pct": 100 * (on_central["gen_cost"] - base_central["gen_cost"]) / base_central["gen_cost"],
            "wait_change_pct": 100 * (own["wait"] - base_own["wait"]) / base_own["wait"],
            "cost_change_pct": 100 * (own["cost"] - base_own["cost"]) / base_own["cost"],
            "max_phi": own["max_phi"], "fleet_needed": own["fleet_needed"],
            "headway_spread": own["headway_spread"],
            "plan_fingerprint": provenance.plan_fp(sel["trips"]),
        })
        rows.append(row)
        detail[name] = {"per_seed_best": {str(k): v.cid for k, v in sel["per_seed_best"].items()},
                        "selection_log": sel["log"]}
        print(f"{name:<12} {c.cid:<7} own change {row['own_gen_cost_change_pct']:+7.2f}%  "
              f"central-scale change {row['central_gen_cost_change_pct']:+7.2f}%  "
              f"wait {row['wait_change_pct']:+6.1f}%  cost {row['cost_change_pct']:+6.1f}%  "
              f"am {c.headways[0]:.2f}/{c.shares[0]:.2f}", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(out / "sensitivity_layer3.csv", index=False)
    (out / "sensitivity_layer3.json").write_text(json.dumps(
        {"schema_version": 2, "run_id": ctx.run_id, "seeds": SEEDS, "evidence_kind": "exploratory",
         "note": ("One factor at a time from the central profile. For r = 150/90 = 1.667 the cost per "
                  "unit of full capacity is equal for both vehicle types; r = 1.5 and 1.7 bracket it."),
         "rows": rows, "detail": detail, "seconds": time.time() - t0},
        indent=2, default=float), encoding="utf-8")
    print(f"Written: sensitivity_layer3.csv/json ({time.time()-t0:.0f}s) -> {out}")


if __name__ == "__main__":
    main()
