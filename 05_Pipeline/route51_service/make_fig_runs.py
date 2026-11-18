"""Figure 3.3 (thesis §3.4) — full-trip bus duration per run, schema 2.

One point per run (the registered observation unit): the primary value of each
baseline and candidate run of the confirmatory series, plus the candidate at the
boarding speed-up sensitivity levels (0% and 15%), which reuse the candidate seeds
and are shown as exploratory.

Usage: python make_fig_runs.py --output-dir DIR --input runs_confirmatory.csv=PATH
         --input runs_speedup0.csv=PATH --input runs_speedup15.csv=PATH
Outputs: fig3_runs_duration.png
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


def main() -> None:
    ctx = runctx.start(__file__, inputs=["runs_confirmatory.csv", "runs_speedup0.csv", "runs_speedup15.csv"])
    c = pd.read_csv(ctx.input("runs_confirmatory.csv"))
    groups = [("Baseline\n(12 m, 8 min peak)", c[c.condition == "baseline"]["primary_min"]),
              ("Plan, speed-up 30%\n(central)", c[c.condition == "candidate"]["primary_min"]),
              ("Plan, speed-up 15%\n(sensitivity)", pd.read_csv(ctx.input("runs_speedup15.csv"))["primary_min"]),
              ("Plan, speed-up 0%\n(sensitivity)", pd.read_csv(ctx.input("runs_speedup0.csv"))["primary_min"])]
    fig, ax = plt.subplots(figsize=(7.4, 4.3))
    rng = np.random.default_rng(0)
    colors = ["#1f3d7a", "#C44E52", "#DD8452", "#8c8c8c"]
    for i, (lab, v) in enumerate(groups):
        x = i + rng.uniform(-0.12, 0.12, len(v))
        ax.scatter(x, v, s=16, alpha=0.75, color=colors[i], edgecolors="none")
        ax.hlines(v.mean(), i - 0.28, i + 0.28, color=colors[i], lw=2)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels([g[0] for g in groups], fontsize=8.5)
    ax.set_ylabel("Mean full-trip bus duration per run (min)\n07:00–10:00 departures, both directions")
    ax.set_title("One point per SUMO run (30 per condition); bars are condition means", fontsize=10)
    ax.grid(axis="y", alpha=0.25, lw=0.6)
    fig.tight_layout()
    fig.savefig(ctx.out / "fig3_runs_duration.png", dpi=200)
    print("Written: fig3_runs_duration.png")


if __name__ == "__main__":
    main()
