"""NSGA-II convergence study (thesis §3.3.2, Figure 3.2).

Runs the Layer-3 optimizer over several seeds with save_history=True and reports
the hypervolume (HV) curve per generation. A single reference point — 10% worse
than the worst objective value reached by ANY seed — is shared across seeds so the
curves are directly comparable and the mean-with-band plot is meaningful.

Outputs (run directory):
  nsga2_convergence_seeds.csv   HV per (seed, generation), normalised HV, runtime
  nsga2_convergence_summary.csv mean/SD/min/max HV per generation + gen-to-95/99%
  fig3_2_nsga2_convergence.png  Figure 3.2
"""
from __future__ import annotations

import sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from pymoo.indicators.hv import Hypervolume  # noqa: E402

import runctx  # noqa: E402
from layer3.optimize import hv_stabilised, run_nsga2  # noqa: E402
from layer3.study import setup  # noqa: E402

SEEDS = [42, 43, 44, 45, 46]


def plot_convergence(seeds: pd.DataFrame, png: Path, pop: int, gen: int, delta) -> None:
    """Figure 3.2 from the per-seed table alone, so it can be redrawn without re-running."""
    piv = seeds.pivot(index="generation", columns="seed", values="hypervolume")
    x = piv.index.values
    finals = piv.iloc[-1]
    g95 = [int(np.argmax(piv[c].values >= 0.95 * finals[c]) + 1) for c in piv.columns]
    g99 = [int(np.argmax(piv[c].values >= 0.99 * finals[c]) + 1) for c in piv.columns]
    n = len(piv.columns)
    scale = 1e12
    fig, ax = plt.subplots(figsize=(7.2, 4.3))
    for c in piv.columns:
        ax.plot(x, piv[c].values / scale, lw=0.8, color="0.72", zorder=1)
    ax.fill_between(x, piv.min(axis=1) / scale, piv.max(axis=1) / scale,
                    color="#4C72B0", alpha=0.18, lw=0, zorder=2,
                    label=f"min–max across {n} seeds")
    ax.plot(x, piv.mean(axis=1) / scale, color="#1f3d7a", lw=2.2, zorder=3,
            label="mean hypervolume")
    ax.axvline(np.mean(g95), color="#C44E52", ls="--", lw=1.2, zorder=4,
               label=f"95% of final HV (gen {np.mean(g95):.0f})")
    ax.axvline(np.mean(g99), color="#C44E52", ls=":", lw=1.2, zorder=4,
               label=f"99% of final HV (gen {np.mean(g99):.0f})")
    ax.set_xlabel("Generation")
    ax.set_ylabel(r"Hypervolume ($\times 10^{12}$)" "\n"
                  "objective space: pax·min × index × pax·min", fontsize=9)
    ax.set_title("NSGA-II convergence on the Line 51 corridor\n"
                 f"pop = {pop}, {gen} generations, δ = {delta}, {n} seeds",
                 fontsize=10)
    ax.set_xlim(1, gen)
    ax.grid(alpha=0.25, lw=0.6)
    ax.legend(fontsize=8, loc="lower right", framealpha=0.9)
    fig.tight_layout()
    fig.savefig(png, dpi=200)
    plt.close(fig)


def main() -> None:
    ctx = runctx.start(__file__, inputs=["fused_demand_matrix.csv"])
    out = ctx.out
    st = setup(ctx.cfg, pd.read_csv(ctx.input("fused_demand_matrix.csv")))
    POP, GEN = st.pop, st.gen
    summary, params, budget, delta = st.summary, st.params, st.budget, st.delta

    print("=" * 74)
    print(f"NSGA-II CONVERGENCE — {len(SEEDS)} seeds x {GEN} generations (pop={POP}, delta={delta})")
    print("=" * 74)

    runs = []
    for s in SEEDS:
        t0 = time.time()
        res = run_nsga2(summary, params, budget, delta, pop=POP, gen=GEN,
                        seed=s, save_history=True)
        secs = time.time() - t0
        fronts = [np.atleast_2d(h.opt.get("F")) for h in res.history]
        runs.append({"seed": s, "fronts": fronts, "seconds": secs,
                     "n_pareto": len(np.atleast_2d(res.F))})
        print(f"  seed {s}: {secs:5.1f}s   |front| = {runs[-1]['n_pareto']}")

    # One shared reference point so HV is comparable across seeds.
    ref = np.vstack([F for r in runs for F in r["fronts"]]).max(axis=0) * 1.1
    hv_ind = Hypervolume(ref_point=ref)
    print(f"  shared reference point (wait, cost, crowd_min): {np.round(ref, 1)}")

    rows = []
    for r in runs:
        hv = np.array([hv_ind(F) for F in r["fronts"]])
        for g, v in enumerate(hv, start=1):
            rows.append({"seed": r["seed"], "generation": g, "hypervolume": v,
                         "hv_normalised": v / hv[-1]})
        r["hv"] = hv
        r["criterion"] = hv_stabilised(hv)
        print(f"  seed {r['seed']}: protocol criterion {r['criterion']}")
    df = pd.DataFrame(rows)
    df.to_csv(out / "nsga2_convergence_seeds.csv", index=False)

    piv = df.pivot(index="generation", columns="seed", values="hypervolume")
    summ = pd.DataFrame({
        "generation": piv.index,
        "hv_mean": piv.mean(axis=1).values,
        "hv_sd": piv.std(axis=1, ddof=1).values,
        "hv_min": piv.min(axis=1).values,
        "hv_max": piv.max(axis=1).values,
    })
    summ["hv_mean_normalised"] = summ["hv_mean"] / summ["hv_mean"].iloc[-1]
    summ.to_csv(out / "nsga2_convergence_summary.csv", index=False)

    # Generation at which each seed first reaches 95% / 99% of its own final HV.
    g95 = [int(np.argmax(r["hv"] >= 0.95 * r["hv"][-1]) + 1) for r in runs]
    g99 = [int(np.argmax(r["hv"] >= 0.99 * r["hv"][-1]) + 1) for r in runs]
    secs = [r["seconds"] for r in runs]
    npar = [r["n_pareto"] for r in runs]

    print("-" * 74)
    print(f"Generations to 95% of final HV : {g95}  -> mean {np.mean(g95):.1f} "
          f"(range {min(g95)}-{max(g95)})")
    print(f"Generations to 99% of final HV : {g99}  -> mean {np.mean(g99):.1f} "
          f"(range {min(g99)}-{max(g99)})")
    print(f"Pareto-front size              : mean {np.mean(npar):.1f} "
          f"(range {min(npar)}-{max(npar)})")
    print(f"Optimizer runtime              : mean {np.mean(secs):.2f}s "
          f"(range {min(secs):.2f}-{max(secs):.2f}s), {len(SEEDS)} seeds")
    print(f"Final HV (mean +- SD)          : {summ['hv_mean'].iloc[-1]:.4g} "
          f"+- {summ['hv_sd'].iloc[-1]:.3g}")
    print(f"HV spread across seeds at gen {GEN}: "
          f"{100*summ['hv_sd'].iloc[-1]/summ['hv_mean'].iloc[-1]:.2f}% of the mean")

    plot_convergence(df, out / "fig3_2_nsga2_convergence.png", POP, GEN, delta)
    print("-" * 74)
    print("Written: nsga2_convergence_seeds.csv, nsga2_convergence_summary.csv, "
          "fig3_2_nsga2_convergence.png")
    print("=" * 74)


if __name__ == "__main__":
    main()
