"""Figure 3.2 (thesis §3.3.2) redrawn from a finished convergence run.

Reads nsga2_convergence_seeds.csv written by run_convergence.py and draws the same
figure without re-running the optimiser. The run's population, generations and δ
come from the configuration.

Usage: python make_fig_convergence.py --output-dir DIR --run-id ID
         --input nsga2_convergence_seeds.csv=PATH
Outputs: fig3_2_nsga2_convergence.png
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import runctx  # noqa: E402
from run_convergence import plot_convergence  # noqa: E402


def main() -> None:
    ctx = runctx.start(__file__, inputs=["nsga2_convergence_seeds.csv"])
    ops = ctx.cfg["operations"]
    seeds = pd.read_csv(ctx.input("nsga2_convergence_seeds.csv"))
    plot_convergence(seeds, ctx.out / "fig3_2_nsga2_convergence.png",
                     ops["nsga2"]["pop"], ops["nsga2"]["gen"], ops["equity_delta_default"])
    print("Written: fig3_2_nsga2_convergence.png")


if __name__ == "__main__":
    main()
