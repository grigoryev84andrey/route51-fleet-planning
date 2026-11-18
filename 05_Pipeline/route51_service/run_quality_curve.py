"""Graded data-quality sensitivity (thesis §3.6.c).

run_sweep.py evaluates four named degradation scenarios; this script sweeps the
degradation INTENSITY continuously so §3.6 gets a curve rather than four points.
A single scalar q in [0, 1] scales the generator's degradation knobs:

  telecom : p_missing = 0.50q, p_oscillation = 0.40q   (signalling loss / cell ping-pong)
  atto    : p_untapped = 0.40q, inactive_card_rate = 0.20q, token_fragmentation = 0.40q
  combined: both of the above

Three arms are swept — telecom-only, ATTO-only, and combined — so the two input
streams can be attributed separately. This is the sensitivity to input DATA QUALITY,
which replaces the abstract "telecom penetration rate" of the original §3.6 design:
penetration is not a parameter anywhere in the pipeline, whereas signalling loss and
tap loss are.

Outputs (output/): quality_curve.csv, fig3_6_data_quality.png
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

from config import load_config as load_gen_config  # noqa: E402
from geo import build_corridor  # noqa: E402
from feed_atto import generate_atto  # noqa: E402
from feed_telecom import generate_telecom  # noqa: E402

import runctx  # noqa: E402
from layer1.gateway import build_gateway  # noqa: E402
from layer2.telecom_od import build_telecom_od  # noqa: E402
from layer2.alight_inference import chain_alightings  # noqa: E402
from layer2.downscaling import fuse  # noqa: E402
from layer2.validation import validate  # noqa: E402

LEVELS = [0.0, 0.25, 0.50, 0.75, 1.0]
ARMS = ["telecom", "atto", "combined"]


def _degradation(arm: str, q: float) -> dict:
    tel = {"p_missing": 0.50 * q, "p_oscillation": 0.40 * q}
    att = {"p_untapped": 0.40 * q, "inactive_card_rate": 0.20 * q,
           "token_fragmentation": 0.40 * q}
    if arm == "telecom":
        return {"telecom": tel}
    if arm == "atto":
        return {"atto": att}
    return {"telecom": tel, "atto": att}


def main() -> None:
    gcfg0 = load_gen_config(GEN / "config.yaml")
    sa = gcfg0["study_area"]
    corr = build_corridor(stops_csv=f"{gcfg0['run']['data_dir']}/{sa['stops_csv']}",
                          res=sa["h3_resolution"], buffer_m=sa["catchment_buffer_m"],
                          anchors=gcfg0["attraction_anchors"],
                          cordon_zones=sa["cordon_zones"],
                          density_model=gcfg0["population"]["home_density_model"])
    persons = pd.read_parquet(GEN / "output/persons.parquet")
    trips = pd.read_parquet(GEN / "output/trips.parquet")
    oracle = pd.read_csv(GEN / "output/ground_truth_bus_od.csv")

    ctx = runctx.start(__file__)
    gateway, _ = build_gateway(ctx.args.config.resolve())
    KEY = gateway.cfg["service"]["api_key"]
    out = ctx.out

    print("=" * 100)
    print(f"GRADED DATA-QUALITY SWEEP (3.6.c) - arms={ARMS}, levels={LEVELS}")
    print("=" * 100)

    rows = []
    for arm in ARMS:
        for q in LEVELS:
            t0 = time.time()
            gcfg = copy.deepcopy(gcfg0)
            for feed, kv in _degradation(arm, q).items():
                gcfg["degradation"][feed].update(kv)
            rng = np.random.default_rng(gcfg["run"]["random_seed"])

            atto = generate_atto(gcfg, persons, trips, rng)
            tel = generate_telecom(gcfg, persons, trips, corr, rng)
            ra = gateway.ingest("atto", atto, api_key=KEY)
            rt = gateway.ingest("telecom", tel, api_key=KEY)
            if not (ra.accepted and rt.accepted):
                print(f"  [{arm}/{q}] quarantined — skipped"); continue

            telecom_od = build_telecom_od(rt.index.indexed, sample_rate=0.138)
            chain = chain_alightings(ra.index.indexed)
            fused = fuse(ra.index.indexed, telecom_od, chain, gateway.ref, walk_m=400)
            m, _ = validate(fused, oracle)

            rows.append({"arm": arm, "q": q,
                         "atto_taps": len(atto), "telecom_events": len(tel),
                         "alight_cosine_wtd": m["alight_cosine_wtd"],
                         "top20_od_recall": m["top20_od_recall"],
                         "od_mae": m["od_mae"],
                         "total_error_pct": m["total_error_pct"],
                         "seconds": round(time.time() - t0, 1)})
            r = rows[-1]
            print(f"  [{arm:<9} q={q:.2f}] taps={r['atto_taps']:>5} "
                  f"events={r['telecom_events']:>7}  cos={r['alight_cosine_wtd']:.3f}  "
                  f"recall={r['top20_od_recall']:.2f}  mae={r['od_mae']:.3f}  "
                  f"vol_err={r['total_error_pct']:>6}%  ({r['seconds']}s)")

    df = pd.DataFrame(rows)
    df.to_csv(out / "quality_curve.csv", index=False)

    print("-" * 100)
    for arm in ARMS:
        d = df[df["arm"] == arm]
        if d.empty:
            continue
        print(f"  {arm:<9}: cosine {d['alight_cosine_wtd'].iloc[0]:.3f} -> "
              f"{d['alight_cosine_wtd'].iloc[-1]:.3f}  "
              f"(Δ {d['alight_cosine_wtd'].iloc[-1]-d['alight_cosine_wtd'].iloc[0]:+.3f});  "
              f"recall {d['top20_od_recall'].iloc[0]:.2f} -> {d['top20_od_recall'].iloc[-1]:.2f}")

    # --- Figure: recovery accuracy vs degradation intensity ---
    colours = {"telecom": "#4C72B0", "atto": "#DD8452", "combined": "#C44E52"}
    marks = {"telecom": "o", "atto": "s", "combined": "D"}
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.2), sharex=True)
    for arm in ARMS:
        d = df[df["arm"] == arm].sort_values("q")
        if d.empty:
            continue
        ax1.plot(100 * d["q"], d["alight_cosine_wtd"], marks[arm] + "-",
                 color=colours[arm], lw=1.9, ms=6, label=arm)
        ax2.plot(100 * d["q"], d["top20_od_recall"], marks[arm] + "-",
                 color=colours[arm], lw=1.9, ms=6, label=arm)

    ax1.set_ylabel("Alighting-distribution cosine\n(vs generator oracle)")
    ax1.set_title("Alighting recovery", fontsize=10)
    ax2.set_ylabel("Top-20 OD recall")
    ax2.set_title("Recovery of the dominant OD pairs", fontsize=10)
    for ax in (ax1, ax2):
        ax.set_xlabel("Degradation intensity q (%)")
        ax.grid(alpha=0.25, lw=0.6)
        ax.legend(fontsize=8.5, title="degraded feed", title_fontsize=8.5)
    ax1.set_ylim(0.5, 1.0)
    ax2.set_ylim(0, 1.05)
    fig.suptitle("Sensitivity of Layer-2 demand recovery to telecom / ATTO input quality",
                 fontsize=10.5)
    fig.tight_layout()
    fig.savefig(out / "fig3_6_data_quality.png", dpi=200)
    print("Written: quality_curve.csv, fig3_6_data_quality.png")
    print("=" * 100)


if __name__ == "__main__":
    main()
