"""Convergence + budget check across the two NSGA-II parameter settings.

§2.5.1 of the thesis specifies pop = 120, gen = 300; the implementation that
produced the reported plans runs pop = 60, gen = 60. This script runs both over
the same seeds and reports (a) the HV curve for each and (b) whether the larger
budget changes the recommended plan or its generalised cost — i.e. whether the
60x60 setting is converged in the sense that matters for the results.

Outputs (run directory):
  nsga2_config_comparison.csv    per (config, seed): final HV, gens to 95/99%, runtime, KPI of recommended plan
  nsga2_convergence_configs.csv  HV per (config, seed, generation)
"""
from __future__ import annotations

import sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from pymoo.indicators.hv import Hypervolume  # noqa: E402

import runctx  # noqa: E402
from layer3.operations import evaluate_plan  # noqa: E402
from layer3.optimize import recommend, run_nsga2  # noqa: E402
from layer3.study import setup  # noqa: E402

SEEDS = [42, 43, 44, 45, 46]
CONFIGS = [("implemented", 60, 60), ("thesis_2.5.1", 120, 300)]


def main() -> None:
    ctx = runctx.start(__file__, inputs=["fused_demand_matrix.csv"])
    out = ctx.out
    st = setup(ctx.cfg, pd.read_csv(ctx.input("fused_demand_matrix.csv")))
    summary, params, budget, delta = st.summary, st.params, st.budget, st.delta
    base_gc = st.base_rel["gen_cost"]          # relaxed basis, same as the candidates below

    print("=" * 86)
    print("NSGA-II PARAMETER-BUDGET CHECK — implemented (60x60) vs §2.5.1 (120x300)")
    print("=" * 86)
    print(f"Baseline wait + crowd_min (relaxed) = {base_gc:.0f} pax·min/day")

    runs = []
    for name, pop, gen in CONFIGS:
        for s in SEEDS:
            t0 = time.time()
            res = run_nsga2(summary, params, budget, delta, pop=pop, gen=gen,
                            seed=s, save_history=True)
            secs = time.time() - t0
            oh, of, _ = recommend(res, seed=s)
            om = evaluate_plan(oh, of, summary, params)
            runs.append({"config": name, "pop": pop, "gen": gen, "seed": s,
                         "seconds": secs, "n_pareto": len(np.atleast_2d(res.F)),
                         "fronts": [np.atleast_2d(h.opt.get("F")) for h in res.history],
                         "gen_cost": om["gen_cost"],
                         "wait": om["wait"], "cost": om["cost"],
                         "crowd_min": om["crowd_min"]})

    ref = np.vstack([F for r in runs for F in r["fronts"]]).max(axis=0) * 1.1
    hv_ind = Hypervolume(ref_point=ref)
    print(f"Shared reference point (wait, cost, crowd_min): {np.round(ref, 1)}")

    curves, summary_rows = [], []
    for r in runs:
        hv = np.array([hv_ind(F) for F in r["fronts"]])
        for g, v in enumerate(hv, start=1):
            curves.append({"config": r["config"], "seed": r["seed"],
                           "generation": g, "hypervolume": v})
        summary_rows.append({
            "config": r["config"], "pop": r["pop"], "gen": r["gen"], "seed": r["seed"],
            "hv_final": hv[-1],
            "gen_to_95pct": int(np.argmax(hv >= 0.95 * hv[-1]) + 1),
            "gen_to_99pct": int(np.argmax(hv >= 0.99 * hv[-1]) + 1),
            "seconds": round(r["seconds"], 3), "n_pareto": r["n_pareto"],
            "recommended_gen_cost": r["gen_cost"],
            "change_vs_baseline_pct": 100 * (r["gen_cost"] - base_gc) / base_gc,
            "wait": r["wait"], "cost": r["cost"], "crowd_min": r["crowd_min"]})

    pd.DataFrame(curves).to_csv(out / "nsga2_convergence_configs.csv", index=False)
    sm = pd.DataFrame(summary_rows)
    sm.to_csv(out / "nsga2_config_comparison.csv", index=False)

    print("-" * 86)
    for name, pop, gen in CONFIGS:
        g = sm[sm["config"] == name]
        print(f"[{name:<13} pop={pop:>3} gen={gen:>3}]  "
              f"HV_final = {g['hv_final'].mean():.4g} ± {g['hv_final'].std(ddof=1):.3g}  "
              f"({100*g['hv_final'].std(ddof=1)/g['hv_final'].mean():.1f}% CV)")
        print(f"{'':<24}gens to 95% HV = {g['gen_to_95pct'].mean():.1f} "
              f"(range {g['gen_to_95pct'].min()}–{g['gen_to_95pct'].max()});  "
              f"99% = {g['gen_to_99pct'].mean():.1f} "
              f"(range {g['gen_to_99pct'].min()}–{g['gen_to_99pct'].max()})")
        print(f"{'':<24}|Pareto front| = {g['n_pareto'].mean():.0f};  "
              f"runtime = {g['seconds'].mean():.2f}s "
              f"(range {g['seconds'].min():.2f}–{g['seconds'].max():.2f}s)")
        print(f"{'':<24}recommended plan generalised cost = "
              f"{g['recommended_gen_cost'].mean():.0f} ± {g['recommended_gen_cost'].std(ddof=1):.0f}  "
              f"({g['change_vs_baseline_pct'].mean():+.1f}% vs baseline)")
    print("-" * 86)
    a = sm[sm["config"] == "implemented"]["recommended_gen_cost"]
    b = sm[sm["config"] == "thesis_2.5.1"]["recommended_gen_cost"]
    print(f"Effect of the larger budget on the delivered plan: "
          f"{100*(a.mean()-b.mean())/a.mean():+.2f}% generalised cost "
          f"({a.mean():.0f} -> {b.mean():.0f})")
    print("Written: nsga2_config_comparison.csv, nsga2_convergence_configs.csv")
    print("=" * 86)


if __name__ == "__main__":
    main()
