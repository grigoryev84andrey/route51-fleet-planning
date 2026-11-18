"""Contribution controls of protocol route51_v2 (plan S13).

1. Simple rule. Keep the original baseline-headway control for historical
   comparison. For the common *feasible* comparison, divide the 18 service hours
   evenly over the baseline's total departures per direction, then add 18 m
   trips by a deterministic greedy rule (largest reduction of wait + crowd_min
   per unit of added operating cost, ties by period order). Both this rule and
   the central optimiser obey the same budget, fleet and headway-spread cap.
   This additional control was designed after the confirmatory series; it is
   exploratory and cannot retroactively establish optimiser superiority.

2. No telecom prior. The fused matrix is rebuilt from the raw generator feeds
   through Layer 1-2 with the prior weight alpha = 5 (full) and alpha = 0 (no
   prior: trip-chaining evidence only). If the full rebuild does not reproduce the
   frozen matrix, the pair full / no-prior is reported as its own experiment and
   its difference is not attributed to the central result. Plans are optimised on
   each estimate and scored on the full estimate of the same pair.

Nothing here is a causal experiment on real transport.

Outputs (run directory): controls.json, controls.csv
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GEN = ROOT.parent / "route51_gen"
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import runctx  # noqa: E402
from layer1.gateway import build_gateway  # noqa: E402
from layer2.alight_inference import chain_alightings  # noqa: E402
from layer2.downscaling import fuse  # noqa: E402
from layer2.telecom_od import build_telecom_od  # noqa: E402
from layer2.validation import validate  # noqa: E402
from layer3.dispatch import build_dispatch, dispatch_metrics, feasibility  # noqa: E402
from layer3.operations import PERIOD_BLOCKS, PERIOD_NAMES  # noqa: E402
from layer3.study import optimise_one_seed, select_from_candidates, setup  # noqa: E402

SEEDS = (42, 43, 44)


def simple_rule(st, headways=None) -> dict:
    """Greedy integer fleet mix on fixed headways within the common budget."""
    bh = np.asarray(st.bh if headways is None else headways, dtype=float)
    shares = np.zeros(len(PERIOD_NAMES))
    trips = build_dispatch(bh, shares, st.params, label="simple")
    m = dispatch_metrics(trips, st.summary, st.params)
    if m["cost"] > st.budget + 1e-9:
        raise ValueError("the simple rule starts above the common budget")
    grids = []
    for per in PERIOD_NAMES:
        counts = [sum(t.period == per and t.block == block and t.direction == "fwd"
                      for t in trips) for block in range(len(PERIOD_BLOCKS[per]))]
        grids.append(sorted({min(1.0, (n + 0.5) / N + 1e-9)
                             for N in counts if N > 0 for n in range(N)}))
    steps = []
    while True:
        best = None
        for k in range(len(PERIOD_NAMES)):
            if shares[k] >= 1.0 - 1e-12:
                continue
            next_share = next((f for f in grids[k] if f > shares[k] + 1e-12), None)
            if next_share is None:
                continue
            trial = shares.copy()
            trial[k] = next_share
            t = build_dispatch(bh, trial, st.params, label="simple")
            mt = dispatch_metrics(t, st.summary, st.params)
            dcost = mt["cost"] - m["cost"]
            if mt["cost"] > st.budget + 1e-9 or dcost <= 0:
                continue
            gain = (m["gen_cost"] - mt["gen_cost"]) / dcost
            if gain > 0 and (best is None or gain > best[0]):
                best = (gain, k, trial, t, mt)
        if best is None:
            break
        _, k, shares, trips, m = best
        steps.append({"period": PERIOD_NAMES[k], "share": float(shares[k]), "gen_cost": m["gen_cost"],
                      "cost": m["cost"]})
    return {"headways": bh.tolist(), "shares": shares.tolist(), "dispatch": m, "trips": trips,
            "steps": steps}


def equal_frequency_headways(st) -> list[float]:
    """Preserve the baseline's total departure count, spread uniformly over service time."""
    service_minutes = sum((end - start) * 60 for blocks in PERIOD_BLOCKS.values()
                          for start, end in blocks)
    departures = sum(t.direction == "fwd" for t in st.base_trips)
    if departures <= 0:
        raise ValueError("baseline has no departures")
    return [service_minutes / departures] * len(PERIOD_NAMES)


def row(name, m, base, **extra):
    return {"arm": name, "wait": m["wait"], "crowd_min": m["crowd_min"], "gen_cost": m["gen_cost"],
            "cost": m["cost"], "headway_spread": m["headway_spread"], "fleet_needed": m["fleet_needed"],
            "max_phi": m["max_phi"],
            "gen_cost_change_pct": 100 * (m["gen_cost"] - base["gen_cost"]) / base["gen_cost"],
            "wait_change_pct": 100 * (m["wait"] - base["wait"]) / base["wait"],
            "cost_change_pct": 100 * (m["cost"] - base["cost"]) / base["cost"], **extra}


def optimise(pool, cfg, fused, overrides=None):
    futs = [pool.submit(optimise_one_seed, (cfg, fused, None, overrides, s)) for s in SEEDS]
    cands = []
    for f in as_completed(futs):
        cands.extend(f.result())
    return cands


def main() -> None:
    ctx = runctx.start(__file__, inputs=["fused_demand_matrix.csv"])
    cfg, out = ctx.cfg, ctx.out
    t0 = time.time()
    frozen = pd.read_csv(ctx.input("fused_demand_matrix.csv"))
    st = setup(cfg, frozen)
    base = st.base_disp
    result = {"schema_version": 2, "run_id": ctx.run_id, "evidence_kind": "exploratory",
              "seeds": SEEDS, "budget": st.budget}
    rows = []
    with ProcessPoolExecutor(max_workers=6) as pool:
        # ---------------- 1. simple rule vs optimiser --------------------------------
        sr = simple_rule(st)
        sf = simple_rule(st, equal_frequency_headways(st))
        sf_checks = feasibility(sf["dispatch"], st.budget, st.delta, st.params["fleet"])
        if not sf_checks["feasible"]:
            raise RuntimeError(f"equal-frequency simple rule is not feasible: {sf_checks['checks']}")
        nolimit = {"operations": {"equity_delta_default": 999.0}}
        st_nl = setup(cfg, frozen, cfg_overrides=nolimit)
        sel_nl = select_from_candidates(st_nl, optimise(pool, cfg, frozen, nolimit))
        sel_c = select_from_candidates(st, optimise(pool, cfg, frozen))
        rows.append(row("simple_rule_baseline_headways", sr["dispatch"], base,
                        shares=json.dumps(sr["shares"]),
                        within_spread_cap=sr["dispatch"]["headway_spread"] <= st.delta))
        rows.append(row("simple_rule_feasible_equal_frequency", sf["dispatch"], base,
                        headways=json.dumps(sf["headways"]), shares=json.dumps(sf["shares"]),
                        within_spread_cap=True, feasible=True,
                        same_budget_cap=True))
        rows.append(row("optimiser_no_spread_cap", sel_nl["dispatch"], base, cid=sel_nl["candidate"].cid))
        rows.append(row("optimiser_central_capped", sel_c["dispatch"], base, cid=sel_c["candidate"].cid))
        result["simple_rule"] = {"steps": sr["steps"], "shares": sr["shares"],
                                 "note": "historical uncapped comparison; violates the central spread cap"}
        result["feasible_simple_rule"] = {
            "design": "uniform service frequency preserving baseline departures, then greedy 18 m allocation",
            "selected_after_confirmatory_series": True, "evidence_kind": "exploratory",
            "headways": sf["headways"], "shares": sf["shares"], "steps": sf["steps"],
            "checks": sf_checks["checks"], "budget_use_pct": 100 * sf["dispatch"]["cost"] / st.budget}

        # ---------------- 2. telecom prior on / off ------------------------------------
        gateway, _ = build_gateway(ctx.args.config.resolve())
        key = cfg["service"]["api_key"]
        ra = gateway.ingest("atto", pd.read_csv(GEN / "output/atto_taps_raw.csv"), api_key=key)
        rt = gateway.ingest("telecom", pd.read_csv(GEN / "output/telecom_cdr_raw.csv"), api_key=key)
        tod = build_telecom_od(rt.index.indexed, sample_rate=0.138)
        ch = chain_alightings(ra.index.indexed)
        full = fuse(ra.index.indexed, tod, ch, gateway.ref, walk_m=400)
        noprior = fuse(ra.index.indexed, tod, ch, gateway.ref, walk_m=400, alpha=0.0)
        oracle = pd.read_csv(GEN / "output/ground_truth_bus_od.csv")
        key_cols = ["board_stop", "alight_stop", "hour"]
        a = full.sort_values(key_cols).reset_index(drop=True)
        b = frozen.sort_values(key_cols).reset_index(drop=True)
        same_rows = len(a) == len(b) and (a[key_cols].values == b[key_cols].values).all()
        maxdiff = float(np.abs(a["demand"].values - b["demand"].values).max()) if same_rows else None
        reproduces = bool(same_rows and maxdiff is not None and maxdiff < 1e-9)
        q_full, _ = validate(full, oracle, full_precision=True)
        q_np, _ = validate(noprior, oracle, full_precision=True)
        result["telecom_prior"] = {
            "rebuild_reproduces_frozen_matrix": reproduces,
            "rebuild_rows": len(full), "frozen_rows": len(frozen), "max_abs_diff": maxdiff,
            "rebuild_total": float(full["demand"].sum()), "frozen_total": float(frozen["demand"].sum()),
            "quality_full": {k: q_full[k] for k in ("alight_cosine_wtd", "top20_od_recall", "od_mae")},
            "quality_no_prior": {k: q_np[k] for k in ("alight_cosine_wtd", "top20_od_recall", "od_mae")},
            "attribution": ("difference attributable to the central experiment" if reproduces else
                            "separate paired experiment; not attributed to the central frozen-matrix result"),
        }
        full.to_csv(out / "fused_rebuild_full.csv", index=False)
        noprior.to_csv(out / "fused_rebuild_no_prior.csv", index=False)
        st_full = setup(cfg, full)
        sel_full = select_from_candidates(st_full, optimise(pool, cfg, full))
        st_np = setup(cfg, noprior)
        sel_np = select_from_candidates(st_np, optimise(pool, cfg, noprior))
        # score both plans on the full estimate of the same pair
        for name, sel in (("plan_from_full_estimate", sel_full), ("plan_from_no_prior_estimate", sel_np)):
            if sel["candidate"] is None:
                rows.append({"arm": name, "feasible": False})
                continue
            scored = dispatch_metrics(sel["trips"], st_full.summary, st_full.params)
            rows.append(row(name, scored, st_full.base_disp, cid=sel["candidate"].cid,
                            scored_on="full rebuild estimate"))
    pd.DataFrame(rows).to_csv(out / "controls.csv", index=False)
    result["rows"] = rows
    result["seconds"] = time.time() - t0
    (out / "controls.json").write_text(json.dumps(result, indent=2, default=float), encoding="utf-8")
    for r in rows:
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()
               if k in ("arm", "cid", "gen_cost", "gen_cost_change_pct", "wait_change_pct", "cost_change_pct",
                        "headway_spread", "within_spread_cap")})
    print(json.dumps(result["telecom_prior"], indent=1, default=float))
    print(f"({time.time()-t0:.0f}s) -> {out}")


if __name__ == "__main__":
    main()
