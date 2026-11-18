"""Privacy sensitivity: k-anonymity threshold (thesis §3.6.d).

The implemented privacy control is k-anonymity (governance.k_anonymity), NOT
epsilon-differential privacy: no noise mechanism exists in the codebase, so an
epsilon curve cannot be produced. This sweep varies k over {5, 10, 20, 50} and
measures, at each k:

  1. suppression   — share of (cell, time-bin) aggregates and of records withheld;
  2. recovery      — Layer-2 accuracy vs the generator oracle (alighting cosine,
                     top-20 OD recall, OD MAE, total volume error);
  3. optimisation  — the Layer-3 plan re-optimised on the privatised demand, scored
                     on the SAME (un-privatised) oracle-consistent demand the
                     baseline is scored on, so the number is the operational cost
                     of planning from privacy-degraded data.

Run with governance.suppress_downstream = true so the guarantee actually binds on
the demand estimate (see layer1/indexing.py).

Outputs (run directory): kanon_sweep.csv, fig3_7_kanon_sensitivity.png
"""
from __future__ import annotations

import copy, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GEN = ROOT.parent / "route51_gen"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(GEN / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from layer1.gateway import build_gateway  # noqa: E402
from layer2.telecom_od import build_telecom_od  # noqa: E402
from layer2.alight_inference import chain_alightings  # noqa: E402
from layer2.downscaling import fuse  # noqa: E402
from layer2.validation import validate  # noqa: E402
import runctx  # noqa: E402
from layer3.dispatch import dispatch_metrics  # noqa: E402
from layer3.study import setup  # noqa: E402

K_VALUES = [5, 10, 20, 50]
SEEDS = [42, 43, 44]


def main() -> None:
    ctx = runctx.start(__file__, inputs=["fused_demand_matrix.csv"])
    gateway, _ = build_gateway(ctx.args.config.resolve())
    cfg = ctx.cfg
    KEY = cfg["service"]["api_key"]
    out = ctx.out
    ref = gateway.ref

    atto_raw = pd.read_csv(GEN / "output/atto_taps_raw.csv")
    tel_raw = pd.read_csv(GEN / "output/telecom_cdr_raw.csv")
    oracle = pd.read_csv(GEN / "output/ground_truth_bus_od.csv")

    # Reference demand = the un-privatised fusion. Plans are SCORED on this, so the
    # sweep measures the cost of *planning* from degraded data, not a moving target.
    ref_st = setup(cfg, pd.read_csv(ctx.input("fused_demand_matrix.csv")))
    POP, GEN_N, delta = ref_st.pop, ref_st.gen, ref_st.delta
    base_gc = ref_st.base_gen_cost

    print("=" * 96)
    print(f"k-ANONYMITY SENSITIVITY (3.6.d) - k in {K_VALUES}, "
          f"pop={POP}, gen={GEN_N}, delta={delta}, seeds {SEEDS}")
    print("=" * 96)
    print(f"Baseline wait + crowd_min (dispatch, un-privatised demand) = {base_gc:.0f} pax·min/day")
    print("-" * 96)

    rows = []
    for k in K_VALUES:
        t0 = time.time()
        gw = copy.deepcopy(gateway)
        gw.cfg = copy.deepcopy(cfg)
        gw.cfg["governance"]["k_anonymity"] = k
        gw.cfg["governance"]["suppress_downstream"] = True

        ra = gw.ingest("atto", atto_raw.copy(), api_key=KEY)
        rt = gw.ingest("telecom", tel_raw.copy(), api_key=KEY)
        if not (ra.accepted and rt.accepted):
            print(f"  k={k:<3} feed quarantined — skipped"); continue

        sa, st = ra.index.stats, rt.index.stats
        cell_supp = (sa["cells_suppressed"] + st["cells_suppressed"]) / \
                    max(sa["cells_total"] + st["cells_total"], 1)
        atto_rec_supp = sa["records_suppressed"] / max(sa["records_suppressed"] + len(ra.index.indexed), 1)

        # Layer 2 on the privatised feeds.
        telecom_od = build_telecom_od(rt.index.indexed, sample_rate=0.138)
        chain = chain_alightings(ra.index.indexed)
        fused = fuse(ra.index.indexed, telecom_od, chain, ref, walk_m=400)
        m, _ = validate(fused, oracle, full_precision=True)

        # Layer 3 planned on the privatised demand (protocol selection rule) ...
        priv_st = setup(cfg, fused)
        sel = priv_st.select(priv_st.optimise(seeds=SEEDS, budget=ref_st.budget),
                             budget=ref_st.budget)
        if sel["candidate"] is None:
            gc_val, gc_reason = None, "no_feasible_executable_candidate"
        else:
            # ... but SCORED on the un-privatised demand (true operational outcome).
            scored = dispatch_metrics(sel["trips"], ref_st.summary, ref_st.params)
            gc_val, gc_reason = scored["gen_cost"], None

        rows.append({
            "k": k,
            "cells_suppressed_atto": sa["cells_suppressed"],
            "cells_total_atto": sa["cells_total"],
            "cells_suppressed_telecom": st["cells_suppressed"],
            "cells_total_telecom": st["cells_total"],
            "cells_suppressed_pct": round(100 * cell_supp, 2),
            "atto_records_suppressed_pct": round(100 * atto_rec_supp, 2),
            "atto_taps_surviving": int(len(ra.index.indexed)),
            "telecom_events_surviving": int(len(rt.index.indexed)),
            "alight_cosine_wtd": m["alight_cosine_wtd"],
            "top20_od_recall": m["top20_od_recall"],
            "od_mae": m["od_mae"],
            "total_error_pct": m["total_error_pct"],
            "riders_recovered": round(float(fused["demand"].sum()), 1),
            "selected_cid": sel["candidate"].cid if sel["candidate"] is not None else None,
            "gen_cost_on_reference": gc_val,
            "gen_cost_reason": gc_reason,
            "baseline_gen_cost": base_gc,
            "gen_cost_change_pct": (100 * (gc_val - base_gc) / base_gc) if gc_val is not None else None,
            "seconds": round(time.time() - t0, 1),
        })
        r = rows[-1]
        print(f"  k={k:<3} suppressed cells={r['cells_suppressed_pct']:>5.2f}%  "
              f"ATTO taps kept={r['atto_taps_surviving']:>5} "
              f"({100-r['atto_records_suppressed_pct']:.1f}%)  "
              f"cos={r['alight_cosine_wtd']:.3f}  recall={r['top20_od_recall']:.2f}  "
              f"riders={r['riders_recovered']:>7.0f}  "
              f"change={r['gen_cost_change_pct'] if r['gen_cost_change_pct'] is None else round(r['gen_cost_change_pct'], 2)}%   ({r['seconds']}s)")

    df = pd.DataFrame(rows)
    df.to_csv(out / "kanon_sweep.csv", index=False)

    print("-" * 96)
    print(f"Suppression rises {df['cells_suppressed_pct'].min():.2f}% -> "
          f"{df['cells_suppressed_pct'].max():.2f}% of cells across k={K_VALUES[0]}..{K_VALUES[-1]}")
    print(f"Recovery (alighting cosine) moves {df['alight_cosine_wtd'].iloc[0]:.3f} -> "
          f"{df['alight_cosine_wtd'].iloc[-1]:.3f}; "
          f"change in wait + crowd_min {df['gen_cost_change_pct'].iloc[0]} -> "
          f"{df['gen_cost_change_pct'].iloc[-1]} %")

    # --- Figure: privacy-utility frontier ---
    # Categorical x (evenly spaced k) — a log axis renders unwanted minor labels and
    # implies an interpolation between thresholds that the sweep does not sample.
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.6, 4.2))
    x = np.arange(len(df))
    klabels = [str(v) for v in df["k"]]

    ax1.plot(x, df["cells_suppressed_pct"], "o-", color="#C44E52", lw=2, ms=6,
             label="aggregate cells suppressed")
    ax1.plot(x, df["atto_records_suppressed_pct"], "s--", color="#DD8452", lw=1.7, ms=5.5,
             label="ATTO taps withheld")
    ax1.set_xticks(x); ax1.set_xticklabels(klabels)
    ax1.set_xlabel("k-anonymity threshold")
    ax1.set_ylabel("Share suppressed (%)")
    ax1.set_ylim(0, 100)
    ax1.set_title("Privacy cost: suppression rises steeply with k", fontsize=10)
    ax1.grid(alpha=0.25, lw=0.6); ax1.legend(fontsize=8.5, loc="upper left")

    ax2.plot(x, df["alight_cosine_wtd"], "o-", color="#4C72B0", lw=2, ms=6,
             label="alighting cosine")
    ax2.plot(x, df["top20_od_recall"], "^--", color="#55A868", lw=1.7, ms=6,
             label="top-20 OD recall")
    ax2.set_xticks(x); ax2.set_xticklabels(klabels)
    ax2.set_xlabel("k-anonymity threshold")
    ax2.set_ylabel("Layer-2 recovery accuracy")
    ax2.set_ylim(0, 1.05)
    ax2.grid(alpha=0.25, lw=0.6)

    ax2b = ax2.twinx()
    ax2b.plot(x, df["gen_cost_change_pct"], "d-.", color="#8172B3", lw=1.9, ms=6,
              label="change in waiting + crowding vs baseline")
    ax2b.set_ylabel("Change in waiting + crowding disutility (%)")
    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax2b.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, fontsize=8, loc="lower left", framealpha=0.92)
    ax2.set_title("Utility: recovery accuracy and plan outcome", fontsize=10)

    fig.suptitle("Privacy–utility sensitivity to the k-anonymity threshold "
                 "(k-anonymity is the implemented control; no ε-DP mechanism exists)",
                 fontsize=9.5, y=1.0)
    fig.tight_layout()
    fig.savefig(out / "fig3_7_kanon_sensitivity.png", dpi=200, bbox_inches="tight")
    print("Written: kanon_sweep.csv, fig3_7_kanon_sensitivity.png")
    print("=" * 96)


if __name__ == "__main__":
    main()
