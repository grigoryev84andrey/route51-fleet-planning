"""Headway-spread frontier, finely swept (thesis §3.6.a), schema 2.

For each cap delta on the headway spread (max - min) / mean across periods, the
optimiser is re-run with the three central seeds, and the plan is selected by
the protocol rule (wait + crowd_min, first candidate feasible after dispatch).
The frontier shows how much passenger generalised cost (pax·min/day) the cap
costs. The spread cap limits variation of headways between periods only; it is
not a measure of spatial equity. The worst-served period (longest headway) is
reported with it, because a dispersion cap alone can be met by levelling down.

Outputs (run directory): equity_curve.csv, fig3_4_equity_frontier.png
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import runctx  # noqa: E402
from layer3.study import row_for, setup  # noqa: E402

DELTAS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.75, 1.00, 999]
ANCHORS = {0.10, 0.20, 0.30, 999}
SEEDS = (42, 43, 44)


def main() -> None:
    ctx = runctx.start(__file__, inputs=["fused_demand_matrix.csv"])
    out = ctx.out
    st = setup(ctx.cfg, pd.read_csv(ctx.input("fused_demand_matrix.csv")))
    print("=" * 100)
    print(f"HEADWAY-SPREAD FRONTIER — pop={st.pop}, gen={st.gen}, seeds {SEEDS}, "
          f"baseline gen_cost (dispatch) = {st.base_gen_cost:.0f} pax·min/day")
    print("=" * 100)

    rows = []
    for d in DELTAS:
        sel = st.select(st.optimise(seeds=SEEDS, delta=d), delta=d)
        r = row_for(st, sel, delta=d, is_anchor=d in ANCHORS)
        rows.append(r)
        lbl = "unconstrained" if d == 999 else f"{d:.2f}"
        if r["feasible"]:
            print(f"  delta={lbl:<14} gen_cost={r['gen_cost']:>10.0f}  change={r['gen_cost_change_pct']:+7.2f}%  "
                  f"spread={r['headway_spread']:.3f}  worst headway={r['worst_headway_min']:5.1f} min  "
                  f"crowd_min={r['crowd_min']:>9.0f}")
        else:
            print(f"  delta={lbl:<14} no feasible executable candidate")

    df = pd.DataFrame(rows)
    df.to_csv(out / "equity_curve.csv", index=False)

    ok = df[(df["feasible"]) & (df["delta"] != 999)].sort_values("delta")
    unc = df[(df["delta"] == 999) & (df["feasible"])]
    band = ok[(ok["delta"] >= 0.10) & (ok["delta"] <= 0.30)]
    if len(band) >= 2:
        slope = np.polyfit(band["delta"], band["gen_cost"], 1)[0]
        print(f"Slope over delta in [0.10, 0.30]: {slope:,.0f} pax·min/day per unit delta")

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    ax.plot(ok["delta"] * 100, ok["gen_cost"] / 1000, "-", color="#1f3d7a", lw=2,
            label="selected plan (protocol rule)")
    anc = ok[ok["is_anchor"]]
    ax.plot(anc["delta"] * 100, anc["gen_cost"] / 1000, "o", color="#1f3d7a", ms=8, zorder=5,
            label="anchors 10 / 20 / 30%")
    ax.axhline(st.base_gen_cost / 1000, color="#C44E52", ls="--", lw=1.3,
               label=f"baseline timetable ({st.base_gen_cost/1000:.0f}k)")
    if len(unc):
        ax.axhline(unc["gen_cost"].iloc[0] / 1000, color="#55A868", ls=":", lw=1.5,
                   label=f"no spread cap ({unc['gen_cost'].iloc[0]/1000:.0f}k)")
    ax.set_xlabel("Maximum headway spread across periods, (max − min) / mean (%)")
    ax.set_ylabel("Waiting + crowding disutility\n(thousand pax·min/day)")
    ax.set_title("Cost of limiting headway spread on the Line 51 model", fontsize=10)
    ax.grid(alpha=0.25, lw=0.6)
    ax.legend(fontsize=8, loc="upper right", framealpha=0.92)
    fig.tight_layout()
    fig.savefig(out / "fig3_4_equity_frontier.png", dpi=200)
    print("Written: equity_curve.csv, fig3_4_equity_frontier.png")


if __name__ == "__main__":
    main()
