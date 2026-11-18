"""Layer 3 — NSGA-II optimisation over the fused demand matrix (schema 2).

Protocol route51_v2 v1.0: objectives [wait, cost, crowd_min]; three central seeds;
candidate pool = union of their final non-dominated feasible solutions; selection
by wait + crowd_min (ties: cost, then stable id); the selected plan is turned
into an executable dispatch and re-checked; if it fails, the next candidate by the
same rule is tried.

Outputs (in the run directory):
  layer3_result.json      full-precision result: parameters, versions, fingerprints,
                          baseline and candidate (relaxed and dispatch metrics),
                          selection log, convergence, legacy control
  pareto_front.csv        all candidates with seed, index, objectives, plan, rank
  headway_schedule.csv    per-period plan (relaxed headway, desired and actual 18 m share)
  dispatch_baseline.csv   executable trips of the baseline
  dispatch_candidate.csv  executable trips of the selected candidate
  baseline_kpis.csv       baseline metrics (relaxed and dispatch)
  nsga2_convergence.csv   hypervolume per generation and seed on a shared reference
  equity_frontier.csv     best wait + crowd_min per headway-spread cap (seed 42)
  layer3_comparison.csv   exploratory Poisson-resampling comparison of the model KPI
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import result_contracts as rc  # noqa: E402
import runctx  # noqa: E402
from layer3 import provenance  # noqa: E402
from layer3.dispatch import feasibility, select_executable  # noqa: E402
from layer3.evaluate import paired_compare  # noqa: E402
from layer3.operations import PERIOD_NAMES, evaluate_plan  # noqa: E402
from layer3.optimize import (candidates_from, equity_frontier, hv_curve,  # noqa: E402
                             hv_stabilised, rank_candidates, run_nsga2, shared_reference)
from layer3.study import setup  # noqa: E402

SEEDS = (42, 43, 44)


def _fmt(h, f):
    return ", ".join(f"{PERIOD_NAMES[k]}: {h[k]:.2f}min/{f[k]*100:.0f}%18m"
                     for k in range(len(PERIOD_NAMES)))


def jsonable(m: dict) -> dict:
    out = {}
    for k, v in m.items():
        if isinstance(v, np.ndarray):
            out[k] = v.tolist()
        elif isinstance(v, (np.floating, np.integer, np.bool_)):
            out[k] = v.item()
        else:
            out[k] = v
    return out


def optimise(summary, params, budget, delta, pop, gen, seeds):
    runs = {s: run_nsga2(summary, params, budget, delta, pop=pop, gen=gen, seed=s,
                         save_history=True) for s in seeds}
    ref = shared_reference(list(runs.values()))
    conv = {}
    for s, r in runs.items():
        hv, _ = hv_curve(r, ref)
        conv[s] = {"hv": hv, **hv_stabilised(hv)}
    return runs, ref, conv


def main() -> None:
    ctx = runctx.start(__file__, inputs=["fused_demand_matrix.csv"])
    cfg = ctx.cfg
    out = ctx.out
    t0 = time.time()
    op = cfg["operations"]
    fused_raw = pd.read_csv(ctx.input("fused_demand_matrix.csv"))
    st = setup(cfg, fused_raw)                          # demand x scale (SCENARIO, labelled)
    stop_ids, params, summary, scale = st.stop_ids, st.params, st.summary, st.scale
    fused = fused_raw.copy()
    fused["demand"] = fused["demand"] * scale

    print("=" * 78)
    print("LAYER 3 — NSGA-II, schema 2 (crowding: "
          f"{params['crowding']['model']}, theta {params['crowding']['theta']}, "
          f"lambda(1) {params['crowding']['lambda_full']})")
    print("=" * 78)
    print(f"Demand scenario : x{scale} on fused demand ({summary['_total_demand']:.1f} boardings/day)")

    # --- baseline ---------------------------------------------------------------
    bh, bf, b_rel, b_trips, b_disp = st.bh, st.bf, st.base_rel, st.base_trips, st.base_disp
    budget, delta = st.budget, st.delta
    b_feas = feasibility(b_disp, budget, delta, params["fleet"])
    print(f"Baseline        : {_fmt(bh, bf)}")
    print(f"  relaxed  wait={b_rel['wait']:.0f} crowd_min={b_rel['crowd_min']:.0f} cost={b_rel['cost']:.1f}")
    print(f"  dispatch wait={b_disp['wait']:.0f} crowd_min={b_disp['crowd_min']:.0f} "
          f"cost={b_disp['cost']:.1f}  feasible={b_feas['feasible']} {b_feas['checks']}")
    print(f"Budget (cost cap): {budget:.1f} = baseline x {op['tco_factor']};  headway spread cap {delta}")

    # --- optimisation, three central seeds -------------------------------------
    ga = op.get("nsga2", {})
    POP, GEN = int(ga.get("pop", 120)), int(ga.get("gen", 300))
    t_opt = time.time()
    runs, hv_ref, conv = optimise(summary, params, budget, delta, POP, GEN, SEEDS)
    gen_used = GEN
    conv_first = None
    if not all(c["stabilised"] for c in conv.values()):
        print("  convergence criterion not met at", GEN, "generations -> one repeat with 600 (protocol)")
        conv_first = {str(s): {k: v for k, v in c.items() if k != "hv"} for s, c in conv.items()}
        gen_used = 600
        runs, hv_ref, conv = optimise(summary, params, budget, delta, POP, 600, SEEDS)
    opt_seconds = time.time() - t_opt
    for s, c in conv.items():
        g = c["rel_gain"]
        print(f"  seed {s}: HV={c['hv'][-1]:.6g}  rel. gain last 20% = "
              f"{'n/a' if g is None else f'{100*g:.3f}%'}  stabilised={c['stabilised']}")

    pool = []
    for s, r in runs.items():
        pool.extend(candidates_from(r, s))
    ranked = rank_candidates(pool)
    if not ranked:
        raise SystemExit("NSGA-II found no feasible plan in any seed")

    cand, c_trips, c_disp, sel_log = select_executable(ranked, summary, params, budget, delta,
                                                       label="candidate")
    per_seed_best = {}
    for s, r in runs.items():
        rs = rank_candidates(candidates_from(r, s))
        per_seed_best[str(s)] = ({"cid": rs[0].cid, "gen_cost": rs[0].gen_cost, "wait": rs[0].wait,
                                  "cost": rs[0].cost, "crowd_min": rs[0].crowd_min,
                                  "headways": rs[0].headways.tolist(),
                                  "shares": rs[0].shares.tolist()} if rs else None)

    # --- write the front ------------------------------------------------------------
    rank_of = {c.cid: i for i, c in enumerate(ranked)}
    rows = []
    for c in pool:
        seed, idx = c.cid.split(":")
        row = {"cid": c.cid, "seed": int(seed), "index": int(idx), "rank": rank_of[c.cid],
               "wait": c.wait, "cost": c.cost, "crowd_min": c.crowd_min, "gen_cost": c.gen_cost}
        row.update({f"h_{p}": c.headways[k] for k, p in enumerate(PERIOD_NAMES)})
        row.update({f"share18_{p}": c.shares[k] for k, p in enumerate(PERIOD_NAMES)})
        rows.append(row)
    pd.DataFrame(rows).sort_values("rank").to_csv(out / "pareto_front.csv", index=False)

    conv_rows = []
    for s, c in conv.items():
        for g, v in enumerate(c["hv"], start=1):
            conv_rows.append({"seed": s, "generation": g, "hypervolume": float(v)})
    pd.DataFrame(conv_rows).to_csv(out / "nsga2_convergence.csv", index=False)

    pd.DataFrame([t.as_dict() for t in b_trips]).to_csv(out / "dispatch_baseline.csv", index=False)
    bk = {f"relaxed_{k}": v for k, v in b_rel.items() if not isinstance(v, (dict, list, np.ndarray))}
    bk.update({f"dispatch_{k}": v for k, v in b_disp.items()
               if not isinstance(v, (dict, list, np.ndarray))})
    bk.update({"demand_scale": scale, "budget": budget, "crowding_model": params["crowding"]["model"]})
    pd.DataFrame([bk]).to_csv(out / "baseline_kpis.csv", index=False)

    result = {
        "schema_version": rc.SCHEMA_VERSION,
        "run_id": ctx.run_id,
        "evidence_kind": "model_only",
        "model_versions": provenance.model_versions(),
        "fingerprints": {
            "input": provenance.input_fp(fused_raw, cfg),
            "plan_baseline": provenance.plan_fp(b_trips),
            "plan_candidate": provenance.plan_fp(c_trips) if c_trips else None,
            "model": provenance.model_fp(),
        },
        "parameters": {
            "demand_scale": scale, "budget": budget, "tco_factor": op["tco_factor"],
            "delta": delta, "fleet": params["fleet"], "cycle_min": params["cycle_min"],
            "crowding": params["crowding"], "seg_min": params["seg_min"].tolist(),
            "capacity": {"bus_12m": params["cap_small"], "bus_18m": params["cap_large"]},
            "relative_cost": {"small": params["veh_cost_small"], "large": params["veh_cost_large"]},
            "nsga2": {"pop": POP, "gen_configured": GEN, "gen_used": gen_used, "seeds": list(SEEDS)},
            "stop_ids": stop_ids,
        },
        "baseline": {"headways": bh.tolist(), "shares": bf.tolist(),
                     "relaxed_metrics": jsonable(b_rel), "dispatch_metrics": jsonable(b_disp),
                     "feasibility": b_feas},
        "candidate": None,
        "selection": {"rule": "min wait + crowd_min; ties: cost, then stable id",
                      "pool_size": len(pool), "examined": sel_log, "per_seed_best": per_seed_best},
        "convergence": {"reference_point": hv_ref.tolist(),
                        "per_seed": {str(s): {k: v for k, v in c.items() if k != "hv"}
                                     for s, c in conv.items()},
                        "first_attempt_if_repeated": conv_first,
                        "optimizer_seconds": opt_seconds},
        "operational_feasibility": "unknown",
    }

    if cand is None:
        print("NO FEASIBLE EXECUTABLE CANDIDATE after dispatch re-evaluation.")
        result["candidate"] = {"status": "none_feasible_after_dispatch"}
    else:
        c_rel = evaluate_plan(cand.headways, cand.shares, summary, params)
        c_feas = feasibility(c_disp, budget, delta, params["fleet"])
        result["candidate"] = {"cid": cand.cid, "headways": cand.headways.tolist(),
                               "shares": cand.shares.tolist(),
                               "relaxed_metrics": jsonable(c_rel),
                               "dispatch_metrics": jsonable(c_disp), "feasibility": c_feas}
        pd.DataFrame([t.as_dict() for t in c_trips]).to_csv(out / "dispatch_candidate.csv", index=False)
        act = {(r["period"], r["direction"]): r for r in c_disp["by_period_direction"]}
        pd.DataFrame({
            "period": PERIOD_NAMES,
            "baseline_headway_min": bh,
            "opt_headway_min": cand.headways,
            "opt_share_18m": cand.shares,
            "opt_trips_per_direction": [act[(p, "fwd")]["trips"] for p in PERIOD_NAMES],
            "opt_trips_18m_per_direction": [act[(p, "fwd")]["trips_18m"] for p in PERIOD_NAMES],
            "opt_share_18m_actual": [act[(p, "fwd")]["share_18m_actual"] for p in PERIOD_NAMES],
            "opt_effective_headway_min": [c_disp["effective_headways"][p] for p in PERIOD_NAMES],
            "opt_mean_capacity": [act[(p, "fwd")]["mean_capacity"] for p in PERIOD_NAMES],
        }).to_csv(out / "headway_schedule.csv", index=False)
        print("-" * 78)
        print(f"Selected        : {cand.cid}  {_fmt(cand.headways, cand.shares)}")
        print(f"  relaxed  wait={c_rel['wait']:.0f} crowd_min={c_rel['crowd_min']:.0f} cost={c_rel['cost']:.1f}")
        print(f"  dispatch wait={c_disp['wait']:.0f} crowd_min={c_disp['crowd_min']:.0f} "
              f"cost={c_disp['cost']:.1f} fleet={c_disp['fleet_needed']} spread={c_disp['headway_spread']:.3f}")
        print(f"  examined {len(sel_log)} candidate(s) in protocol order")

        # legacy control on the same two plans (historical proxy, weight 10)
        result["legacy_control"] = {
            "definition": "wait + 10 * legacy_overcrowd_proxy (historical); not comparable with crowd_min",
            "baseline": b_rel["wait"] + 10 * b_rel["legacy_overcrowd_proxy"],
            "candidate": c_rel["wait"] + 10 * c_rel["legacy_overcrowd_proxy"],
        }
        stat = paired_compare(fused, stop_ids, params, bh, bf, cand.headways, cand.shares, n=60)
        pd.DataFrame([stat]).to_csv(out / "layer3_comparison.csv", index=False)
        result["model_resampling_comparison"] = stat

    # --- headway-spread frontier (seed 42) -------------------------------------------
    front = equity_frontier(summary, params, budget, op["equity_delta_sweep"],
                            pop=POP, gen=gen_used, seed=SEEDS[0])
    pd.DataFrame(front).to_csv(out / "equity_frontier.csv", index=False)
    result["headway_spread_frontier"] = front

    (out / "layer3_result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                            encoding="utf-8")
    print("-" * 78)
    print(f"Elapsed: {time.time()-t0:.1f}s   -> {out}")
    print("=" * 78)


if __name__ == "__main__":
    main()
