"""Figure 3.1 — per-run peak-hour travel time, Baseline vs NSGA-II (§3.4).

Regenerated from replications_raw.csv so the figure matches the executed n=30 run
on the converged Layer-3 plan. Also writes the exact test statistics for §3.4,
because replications_stats.csv rounds p-values to six decimals and both tests here
land far below that.

Outputs (output/): fig3_1_travel_time.png, sec34_stats.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats as st  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from layer1.gateway import load_service_config  # noqa: E402

LABELS = {"baseline": "Baseline\n(12 m, 9 min)", "optimized": "NSGA-II\n(18 m, 11.4 min)"}
COLOURS = {"baseline": "#C44E52", "optimized": "#4C72B0"}


def main() -> None:
    cfg = load_service_config(ROOT / "service_config.yaml")
    out = Path(cfg["paths"]["output_dir_abs"])
    raw = pd.read_csv(out / "replications_raw.csv")
    sched = pd.read_csv(out / "headway_schedule.csv").set_index("period")
    am = sched.loc["am_peak"]
    # Both labels come from the schedule so the axis cannot drift from the run —
    # the baseline headway was previously hard-coded and survived a re-baselining.
    LABELS["baseline"] = f"Baseline\n(12 m, {am['baseline_headway_min']:.0f} min)"
    LABELS["optimized"] = (f"NSGA-II\n({'18' if am['opt_share_18m'] >= 0.5 else '12'} m, "
                           f"{am['opt_headway_min']:.1f} min)")

    B = raw[raw.condition == "baseline"]["mean_travel_min"].dropna().to_numpy()
    O = raw[raw.condition == "optimized"]["mean_travel_min"].dropna().to_numpy()

    # Exact statistics (the CSV rounds p to 1e-6; both tests are far below that).
    welch = st.ttest_ind(B, O, equal_var=False)
    mwu = st.mannwhitneyu(B, O, alternative="two-sided")
    sh_b, sh_o = st.shapiro(B).pvalue, st.shapiro(O).pvalue
    lev = st.levene(B, O).pvalue
    psd = np.sqrt(((B.size - 1) * B.var(ddof=1) + (O.size - 1) * O.var(ddof=1)) /
                  (B.size + O.size - 2))
    d = (B.mean() - O.mean()) / psd
    se = np.sqrt(B.var(ddof=1) / B.size + O.var(ddof=1) / O.size)
    dfw = se**4 / ((B.var(ddof=1) / B.size)**2 / (B.size - 1) +
                   (O.var(ddof=1) / O.size)**2 / (O.size - 1))
    tcrit = st.t.ppf(0.975, dfw)
    diff = B.mean() - O.mean()

    cvB = raw[raw.condition == "baseline"]["headway_cv"].dropna().to_numpy()
    cvO = raw[raw.condition == "optimized"]["headway_cv"].dropna().to_numpy()
    mwu_cv = st.mannwhitneyu(cvB, cvO, alternative="two-sided")

    stats = {
        "n_per_condition": B.size,
        "shapiro_p_baseline": sh_b, "shapiro_p_optimized": sh_o, "levene_p": lev,
        "welch_t": welch.statistic, "welch_df": dfw, "welch_p": welch.pvalue,
        "mannwhitney_U": mwu.statistic, "mannwhitney_p": mwu.pvalue,
        "cohens_d": d,
        "mean_baseline": B.mean(), "mean_optimized": O.mean(),
        "mean_diff": diff, "diff_pct": 100 * diff / B.mean(),
        "diff_ci95_low": diff - tcrit * se, "diff_ci95_high": diff + tcrit * se,
        "max_baseline": B.max(), "max_optimized": O.max(),
        "cv_mannwhitney_U": mwu_cv.statistic, "cv_mannwhitney_p": mwu_cv.pvalue,
        "cv_reduction_pct": 100 * (cvB.mean() - cvO.mean()) / cvB.mean(),
    }
    pd.DataFrame([stats]).to_csv(out / "sec34_stats.csv", index=False)

    for k, v in stats.items():
        print(f"  {k:22} {v:.6g}" if isinstance(v, float) else f"  {k:22} {v}")

    # --- Figure 3.1 ---
    fig, ax = plt.subplots(figsize=(6.6, 4.4))
    data = [B, O]
    keys = ["baseline", "optimized"]
    bp = ax.boxplot(data, positions=[1, 2], widths=0.42, showfliers=False,
                    medianprops=dict(color="0.15", lw=1.6),
                    boxprops=dict(lw=1.1), whiskerprops=dict(lw=1.1),
                    capprops=dict(lw=1.1), patch_artist=True)
    for patch, k in zip(bp["boxes"], keys):
        patch.set_facecolor(COLOURS[k]); patch.set_alpha(0.28)

    rng = np.random.default_rng(0)
    for pos, arr, k in zip([1, 2], data, keys):
        x = pos + rng.uniform(-0.13, 0.13, arr.size)
        ax.scatter(x, arr, s=17, color=COLOURS[k], alpha=0.75, edgecolors="none", zorder=3)
        ax.scatter([pos], [arr.mean()], marker="D", s=52, color="white",
                   edgecolors=COLOURS[k], linewidths=1.6, zorder=5)

    ax.set_xticks([1, 2])
    ax.set_xticklabels([LABELS[k] for k in keys])
    ax.set_ylabel("Peak-hour mean bus travel time (min)")
    ax.set_title(f"Peak-hour travel time per replication ({B.size} runs per condition)\n"
                 f"Δ = {diff:.2f} min ({100*diff/B.mean():.1f}%), Cohen's d = {d:.2f}",
                 fontsize=10)
    ax.grid(axis="y", alpha=0.25, lw=0.6)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(out / "fig3_1_travel_time.png", dpi=200)
    print("Written: fig3_1_travel_time.png, sec34_stats.csv")


if __name__ == "__main__":
    main()
