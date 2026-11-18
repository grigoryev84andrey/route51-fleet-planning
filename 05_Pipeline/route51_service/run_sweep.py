"""Degradation sweep: recovery error as a function of data quality (the thesis result).

Runs the full chain in-process for each scenario:
  generate feeds (with degradation) -> Layer-1 gateway (govern + index) -> Layer-2 fusion -> validate vs oracle.
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

# generator side
from config import load_config as load_gen_config  # noqa: E402
from geo import build_corridor  # noqa: E402
from feed_atto import generate_atto  # noqa: E402
from feed_telecom import generate_telecom  # noqa: E402
# service side
import runctx  # noqa: E402
from layer1.gateway import build_gateway  # noqa: E402
from layer2.telecom_od import build_telecom_od  # noqa: E402
from layer2.alight_inference import chain_alightings  # noqa: E402
from layer2.downscaling import fuse  # noqa: E402
from layer2.validation import validate  # noqa: E402

SCENARIOS = {
    "clean":            {},
    "telecom_noise":    {"telecom": {"p_missing": 0.3, "p_oscillation": 0.2}},
    "atto_loss":        {"atto": {"p_untapped": 0.2, "inactive_card_rate": 0.1, "token_fragmentation": 0.2}},
    "combined_heavy":   {"telecom": {"p_missing": 0.4, "p_oscillation": 0.3},
                         "atto": {"p_untapped": 0.3, "token_fragmentation": 0.3}},
}


def main() -> None:
    gcfg0 = load_gen_config(GEN / "config.yaml")
    sa = gcfg0["study_area"]
    corr = build_corridor(stops_csv=f"{gcfg0['run']['data_dir']}/{sa['stops_csv']}",
                          res=sa["h3_resolution"], buffer_m=sa["catchment_buffer_m"],
                          anchors=gcfg0["attraction_anchors"], cordon_zones=sa["cordon_zones"],
                          density_model=gcfg0["population"]["home_density_model"])
    persons = pd.read_parquet(GEN / "output/persons.parquet")
    trips = pd.read_parquet(GEN / "output/trips.parquet")
    oracle = pd.read_csv(GEN / "output/ground_truth_bus_od.csv")

    ctx = runctx.start(__file__)
    gateway, _ = build_gateway(ctx.args.config.resolve())
    KEY = gateway.cfg["service"]["api_key"]

    rows = []
    print("=" * 88)
    print("LAYER-2 DEGRADATION SWEEP — recovery vs data quality")
    print("=" * 88)
    for name, deg in SCENARIOS.items():
        t0 = time.time()
        gcfg = copy.deepcopy(gcfg0)
        for feed, kv in deg.items():
            gcfg["degradation"][feed].update(kv)
        rng = np.random.default_rng(gcfg["run"]["random_seed"])

        atto = generate_atto(gcfg, persons, trips, rng)
        tel = generate_telecom(gcfg, persons, trips, corr, rng)

        ra = gateway.ingest("atto", atto, api_key=KEY)
        rt = gateway.ingest("telecom", tel, api_key=KEY)
        if not (ra.accepted and rt.accepted):
            print(f"[{name}] feed quarantined — skipped"); continue

        telecom_od = build_telecom_od(rt.index.indexed, sample_rate=0.138)
        chain = chain_alightings(ra.index.indexed)
        fused = fuse(ra.index.indexed, telecom_od, chain, gateway.ref, walk_m=400)
        m, _ = validate(fused, oracle)
        m["scenario"] = name
        m["atto_taps"] = len(atto)
        m["telecom_events"] = len(tel)
        rows.append(m)
        print(f"[{name:<15}] taps={len(atto):>5} events={len(tel):>7}  "
              f"alight_cos={m['alight_cosine_wtd']:.3f}  recall={m['top20_od_recall']:.2f}  "
              f"od_mae={m['od_mae']:.3f}  tot_err%={m['total_error_pct']:>5}  ({time.time()-t0:.0f}s)")

    df = pd.DataFrame(rows)[["scenario", "atto_taps", "telecom_events", "alight_cosine_wtd",
                             "top20_od_recall", "od_mae", "boarding_mae", "total_error_pct"]]
    out = ctx.out / "layer2_sweep.csv"
    df.to_csv(out, index=False)
    print("-" * 88)
    print(df.to_string(index=False))
    print("-" * 88)
    print(f"Written: {out}")
    print("=" * 88)


if __name__ == "__main__":
    main()
