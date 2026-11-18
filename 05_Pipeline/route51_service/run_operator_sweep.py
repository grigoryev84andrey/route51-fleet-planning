"""Operator-share sensitivity (thesis §3.2.2 cross-reference, reported in §3.6.3).

§3.2.2 states that the operator market weights are an uncertain input whose influence
is examined in §3.6. This is that experiment. Two things are varied:

  1. WHICH operator is observed. A real CDR extract comes from one network, and the
     four Uzbek operators differ in demographic skew (D1), so the identity of the
     observed operator is itself a sampling choice with consequences.
  2. The observed operator's SHARE, swept across the uncertainty band D1 reports for
     it (roughly 20-28% each, no clear leader), which changes how many subscribers
     the telecom feed sees.

Recovery is scored against the generator's ground-truth OD oracle, so the question
answered is: how much does the choice of operator, and the uncertainty in its share,
move the demand estimate that Layer 3 optimises on?

Outputs (output/): operator_sweep.csv, fig3_8_operator_sensitivity.png
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
from population import synth_population  # noqa: E402
# activities are reused from the base run; only the operator assignment changes
from feed_atto import generate_atto  # noqa: E402
from feed_telecom import generate_telecom  # noqa: E402

import runctx  # noqa: E402
from layer1.gateway import build_gateway  # noqa: E402
from layer2.telecom_od import build_telecom_od  # noqa: E402
from layer2.alight_inference import chain_alightings  # noqa: E402
from layer2.downscaling import fuse  # noqa: E402
from layer2.validation import validate  # noqa: E402

OPERATORS = ["Beeline", "Ucell", "Uzmobile", "Mobiuz"]
SHARE_POINTS = ["low", "central", "high"]


def main() -> None:
    gcfg0 = load_gen_config(GEN / "config.yaml")
    sa = gcfg0["study_area"]
    corr = build_corridor(stops_csv=f"{gcfg0['run']['data_dir']}/{sa['stops_csv']}",
                          res=sa["h3_resolution"], buffer_m=sa["catchment_buffer_m"],
                          anchors=gcfg0["attraction_anchors"],
                          cordon_zones=sa["cordon_zones"],
                          density_model=gcfg0["population"]["home_density_model"])
    trips = pd.read_parquet(GEN / "output/trips.parquet")
    oracle = pd.read_csv(GEN / "output/ground_truth_bus_od.csv")

    ctx = runctx.start(__file__)
    gateway, _ = build_gateway(ctx.args.config.resolve())
    KEY = gateway.cfg["service"]["api_key"]
    out = ctx.out
    shares0 = gcfg0["telecom"]["operator_shares"]

    print("=" * 104)
    print(f"OPERATOR-SHARE SENSITIVITY - observed operator in {OPERATORS}, "
          f"share at {SHARE_POINTS} of its D1 band")
    print("=" * 104)
    for op in OPERATORS:
        b = shares0[op]["band"]
        print(f"  {op:10} band {b[0]:.2f}-{b[1]:.2f}  central {shares0[op]['central']:.2f}")
    print("-" * 104)

    rows = []
    for op in OPERATORS:
        band = shares0[op]["band"]
        for point in SHARE_POINTS:
            t0 = time.time()
            gcfg = copy.deepcopy(gcfg0)
            share = {"low": band[0], "central": shares0[op]["central"],
                     "high": band[1]}[point]
            # Re-weight: the observed operator takes `share`, the rest keep their
            # relative proportions over the remainder, so the shares still sum to 1.
            others = {k: v["central"] for k, v in shares0.items() if k != op}
            scale = (1.0 - share) / sum(others.values())
            for k in gcfg["telecom"]["operator_shares"]:
                gcfg["telecom"]["operator_shares"][k]["central"] = (
                    share if k == op else others[k] * scale)
            gcfg["telecom"]["observed_operator"] = op

            rng = np.random.default_rng(gcfg["run"]["random_seed"])
            persons = synth_population(gcfg, corr, rng)
            atto = generate_atto(gcfg, persons, trips, rng)
            tel = generate_telecom(gcfg, persons, trips, corr, rng)

            ra = gateway.ingest("atto", atto, api_key=KEY)
            rt = gateway.ingest("telecom", tel, api_key=KEY)
            if not (ra.accepted and rt.accepted):
                print(f"  [{op}/{point}] quarantined — skipped"); continue

            n_sub = int((persons["operator"] == op).sum())
            obs_share = n_sub / max(int(persons["phone_owner"].sum()), 1)
            telecom_od = build_telecom_od(rt.index.indexed, sample_rate=max(obs_share, 1e-6))
            chain = chain_alightings(ra.index.indexed)
            fused = fuse(ra.index.indexed, telecom_od, chain, gateway.ref, walk_m=400)
            m, _ = validate(fused, oracle)

            rows.append({"operator": op, "share_point": point,
                         "share_set": round(share, 4),
                         "share_realised": round(obs_share, 4),
                         "subscribers": n_sub,
                         "telecom_events": len(tel),
                         "alight_cosine_wtd": m["alight_cosine_wtd"],
                         "top20_od_recall": m["top20_od_recall"],
                         "od_mae": m["od_mae"],
                         "total_error_pct": m["total_error_pct"],
                         "seconds": round(time.time() - t0, 1)})
            r = rows[-1]
            print(f"  [{op:9} {point:7} share={share:.2f}] subs={n_sub:>6} "
                  f"events={len(tel):>7}  cos={r['alight_cosine_wtd']:.3f}  "
                  f"recall={r['top20_od_recall']:.2f}  mae={r['od_mae']:.3f}  "
                  f"({r['seconds']}s)")

    df = pd.DataFrame(rows)
    df.to_csv(out / "operator_sweep.csv", index=False)

    print("-" * 104)
    by_op = df.groupby("operator")["alight_cosine_wtd"].agg(["mean", "min", "max"])
    print("Alighting cosine by observed operator (across its share band):")
    print(by_op.round(4).to_string())
    span_op = by_op["mean"].max() - by_op["mean"].min()
    span_share = df.groupby("share_point")["alight_cosine_wtd"].mean()
    print(f"\nBetween-operator spread (mean cosine): {span_op:.4f}")
    print(f"Within-band spread by share point     : "
          f"{span_share.max() - span_share.min():.4f}")
    print(f"Recall range over the whole sweep      : "
          f"{df['top20_od_recall'].min():.2f}-{df['top20_od_recall'].max():.2f}")

    # --- Figure ---
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.2))
    colours = dict(zip(OPERATORS, ["#4C72B0", "#DD8452", "#55A868", "#C44E52"]))
    for op in OPERATORS:
        d = df[df.operator == op].sort_values("share_realised")
        if d.empty:
            continue
        ax1.plot(100 * d["share_realised"], d["alight_cosine_wtd"], "o-",
                 color=colours[op], lw=1.8, ms=6, label=op)
        ax2.plot(100 * d["share_realised"], d["top20_od_recall"], "o-",
                 color=colours[op], lw=1.8, ms=6, label=op)
    ax1.set_ylabel("Alighting-distribution cosine\n(vs generator oracle)")
    ax1.set_title("Alighting recovery", fontsize=10)
    ax2.set_ylabel("Top-20 OD recall")
    ax2.set_title("Recovery of the dominant OD pairs", fontsize=10)
    for ax in (ax1, ax2):
        ax.set_xlabel("Realised share of phone owners on the observed operator (%)")
        ax.grid(alpha=0.25, lw=0.6)
        ax.legend(fontsize=8.5, title="observed operator", title_fontsize=8.5)
    ax1.set_ylim(0.5, 1.0)
    ax2.set_ylim(0, 1.05)
    fig.suptitle("Sensitivity of Layer-2 demand recovery to which mobile operator "
                 "supplies the CDR extract, and to its market share",
                 fontsize=10.5)
    fig.tight_layout()
    fig.savefig(out / "fig3_8_operator_sensitivity.png", dpi=200)
    print("Written: operator_sweep.csv, fig3_8_operator_sensitivity.png")
    print("=" * 104)


if __name__ == "__main__":
    main()
