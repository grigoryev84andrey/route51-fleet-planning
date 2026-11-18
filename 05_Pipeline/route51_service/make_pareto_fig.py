"""Figure 3.3 — the candidate set produced by Layer 3 (schema 2).

Reads a layer-3 run (pareto_front.csv, layer3_result.json) and shows two
projections: waiting vs operating-cost index (colour = crowding disutility) and
waiting vs crowding disutility, both in pax·min/day. The selected plan is the one
the protocol rule picked after dispatch re-evaluation (read from the result, not
recomputed here); the baseline is shown on its dispatch.

Usage: python make_pareto_fig.py --output-dir DIR --input-dir <layer-3 run dir>
Outputs (run directory): fig3_3_pareto_front.png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import runctx  # noqa: E402


def main() -> None:
    ctx = runctx.start(__file__, inputs=["pareto_front.csv", "layer3_result.json"])
    out = ctx.out
    F = pd.read_csv(ctx.input("pareto_front.csv"))
    res = json.loads(ctx.input("layer3_result.json").read_text(encoding="utf-8"))
    if res.get("schema_version") != 2:
        raise SystemExit("layer3_result.json is not schema 2")
    base = res["baseline"]["dispatch_metrics"]
    budget = res["parameters"]["budget"]
    cand = res["candidate"]
    sel = F[F["cid"] == cand["cid"]] if cand and cand.get("cid") else F.iloc[0:0]
    cm = cand["dispatch_metrics"] if cand and cand.get("cid") else None

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.3))
    sc = ax1.scatter(F["cost"], F["wait"] / 1000, c=F["crowd_min"] / 1000, cmap="viridis_r",
                     s=22, alpha=0.8, edgecolors="none")
    if cm:
        ax1.scatter(cm["cost"], cm["wait"] / 1000, marker="*", s=320, color="#C44E52",
                    edgecolors="white", linewidths=0.8, zorder=5,
                    label=f"selected plan {cand['cid']} (dispatch)")
    ax1.scatter(base["cost"], base["wait"] / 1000, marker="X", s=110, color="#1f3d7a",
                edgecolors="white", linewidths=0.8, zorder=5, label="baseline timetable (dispatch)")
    ax1.axvline(budget, color="0.35", ls="--", lw=1.2, label="maximum operating-cost index")
    ax1.set_xlabel("Operating-cost index (relative vehicle-km)")
    ax1.set_ylabel("Waiting (thousand pax·min/day)")
    ax1.set_title("Waiting vs operating cost (colour = crowding disutility)", fontsize=10)
    ax1.grid(alpha=0.25, lw=0.6)
    ax1.legend(fontsize=7.5, loc="upper right")
    cb = fig.colorbar(sc, ax=ax1, pad=0.02)
    cb.set_label("Crowding disutility (thousand pax·min/day)", fontsize=8)
    cb.ax.tick_params(labelsize=7.5)

    ax2.scatter(F["crowd_min"] / 1000, F["wait"] / 1000, c="#4C72B0", s=22, alpha=0.75,
                edgecolors="none", label=f"candidates, 3 seeds (n = {len(F)})")
    if cm:
        ax2.scatter(cm["crowd_min"] / 1000, cm["wait"] / 1000, marker="*", s=320,
                    color="#C44E52", edgecolors="white", linewidths=0.8, zorder=5,
                    label="selected plan (dispatch)")
    ax2.scatter(base["crowd_min"] / 1000, base["wait"] / 1000, marker="X", s=110,
                color="#1f3d7a", edgecolors="white", linewidths=0.8, zorder=5,
                label="baseline timetable (dispatch)")
    ax2.set_xlabel("Crowding disutility (thousand pax·min/day)")
    ax2.set_ylabel("Waiting (thousand pax·min/day)")
    ax2.set_title("Passenger-side trade-off: waiting vs crowding", fontsize=10)
    ax2.grid(alpha=0.25, lw=0.6)
    ax2.legend(fontsize=7.5, loc="upper right")

    ga = res["parameters"]["nsga2"]
    fig.suptitle(f"NSGA-II candidates on the Line 51 model (population {ga['pop']}, "
                 f"{ga['gen_used']} generations, seeds {ga['seeds']}, "
                 f"headway spread ≤ {res['parameters']['delta']})", fontsize=10)
    fig.tight_layout()
    fig.savefig(out / "fig3_3_pareto_front.png", dpi=200)
    print(f"Candidates: {len(F)}; selected {cand.get('cid') if cand else None}; "
          f"rows matching selected id: {len(sel)}")
    print("Written: fig3_3_pareto_front.png")


if __name__ == "__main__":
    main()
