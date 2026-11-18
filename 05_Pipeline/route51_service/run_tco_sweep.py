"""Operating-cost cap sensitivity (thesis §3.6.b), schema 2.

The cap on the operating-cost index is baseline_cost x tco_factor (base 1.25; the
key name is kept for compatibility — the model computes an operating-cost index,
not a Total Cost of Ownership). tco_factor in {1.125, 1.25, 1.375} is a +-10% move
on the cap. For each value the optimiser is re-run with the three central seeds
and the plan is selected by the protocol rule.

Outputs (run directory): tco_sweep.csv, fig3_5_tco_sensitivity.png
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import runctx  # noqa: E402
from layer3.study import row_for, setup  # noqa: E402

TCO_FACTORS = [1.125, 1.25, 1.375]
SEEDS = (42, 43, 44)


def main() -> None:
    ctx = runctx.start(__file__, inputs=["fused_demand_matrix.csv"])
    out = ctx.out
    st = setup(ctx.cfg, pd.read_csv(ctx.input("fused_demand_matrix.csv")))
    base_cost = st.base_rel["cost"]
    print("=" * 90)
    print(f"OPERATING-COST CAP SENSITIVITY — pop={st.pop}, gen={st.gen}, delta={st.delta}, seeds {SEEDS}")
    print("=" * 90)
    rows = []
    for tf in TCO_FACTORS:
        budget = base_cost * tf
        sel = st.select(st.optimise(seeds=SEEDS, budget=budget), budget=budget)
        r = row_for(st, sel, tco_factor=tf, cap_change_pct=100 * (tf / 1.25 - 1), budget=budget)
        if r["feasible"]:
            r["budget_used_pct"] = 100 * r["cost"] / budget
            print(f"  tco_factor={tf:<6} budget={budget:7.1f}  gen_cost={r['gen_cost']:>10.0f}  "
                  f"change={r['gen_cost_change_pct']:+7.2f}%  budget used={r['budget_used_pct']:.1f}%")
        else:
            print(f"  tco_factor={tf:<6} no feasible executable candidate")
        rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv(out / "tco_sweep.csv", index=False)

    ok = df[df["feasible"]]
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.plot(ok["cap_change_pct"], ok["gen_cost_change_pct"], "o-", color="#1f3d7a", lw=2, ms=7)
    for x, y in zip(ok["cap_change_pct"], ok["gen_cost_change_pct"]):
        ax.annotate(f"{y:+.1f}%", (x, y), textcoords="offset points", xytext=(0, 9),
                    ha="center", fontsize=8.5)
    ax.axhline(0, color="0.5", lw=0.9, ls="--")
    ax.set_xticks(ok["cap_change_pct"])
    ax.set_xticklabels([f"{v:+.0f}%\n(f={tf})" for v, tf in zip(ok["cap_change_pct"], ok["tco_factor"])])
    ax.set_xlabel("Change in the operating-cost cap relative to the base cap (f = 1.25)")
    ax.set_ylabel("Change in waiting + crowding disutility\nvs. baseline timetable (%)")
    ax.set_title("Sensitivity to the maximum operating-cost increase", fontsize=10)
    ax.grid(alpha=0.25, lw=0.6)
    fig.tight_layout()
    fig.savefig(out / "fig3_5_tco_sensitivity.png", dpi=200)
    print("Written: tco_sweep.csv, fig3_5_tco_sensitivity.png")


if __name__ == "__main__":
    main()
