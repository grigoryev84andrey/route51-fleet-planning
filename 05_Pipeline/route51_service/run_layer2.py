"""Layer 2 — demand estimation / fusion -> fused stop-level OD, validated vs oracle."""
from __future__ import annotations

import sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import runctx  # noqa: E402
from layer1.reference import load_reference  # noqa: E402
from layer2.telecom_od import build_telecom_od  # noqa: E402
from layer2.alight_inference import chain_alightings  # noqa: E402
from layer2.downscaling import fuse  # noqa: E402
from layer2.validation import validate  # noqa: E402


def main() -> None:
    t0 = time.time()
    ctx = runctx.start(__file__, inputs=["telecom_indexed.parquet", "atto_indexed.parquet"])
    cfg = ctx.cfg
    ref = load_reference(cfg)
    out = Path(cfg["paths"]["output_dir_abs"])
    walk_m = 400  # access threshold for cell->stop mapping (matches generator demand.walk_access_m)

    # Layer-1 indexed feeds (this service's own output).
    telecom_idx = pd.read_parquet(out / "telecom_indexed.parquet")
    atto_idx = pd.read_parquet(out / "atto_indexed.parquet")
    # Ground-truth oracle (from the generator) — for validation only.
    oracle = pd.read_csv(Path(cfg["_root"]) / cfg["paths"]["layer0_dir"] / "ground_truth_bus_od.csv")

    # Telecom sample rate (phone-owner x observed-operator share) for expansion bookkeeping.
    sample_rate = atto_idx["hashed_card_id"].nunique() / max(len(atto_idx), 1)  # placeholder proxy
    sample_rate = 0.138  # observed-operator subset share of population (informational; cancels in fusion)

    print("=" * 70)
    print("LAYER 2 — Demand estimation (data fusion)")
    print("=" * 70)

    telecom_od = build_telecom_od(telecom_idx, sample_rate)
    print(f"Telecom OD (coarse, cell)  : {len(telecom_od):,} cell-pairs "
          f"({telecom_od.n.sum():,} observed trips)")

    chain = chain_alightings(atto_idx)
    print(f"Trip-chaining evidence     : {len(chain):,} (board,alight,hour) pairs "
          f"({int(chain.c.sum()):,} chained alightings)")

    fused = fuse(atto_idx, telecom_od, chain, ref, walk_m=walk_m)
    print(f"Fused stop-level OD        : {len(fused):,} (board,alight,hour) cells "
          f"({fused.demand.sum():.0f} riders)")
    fused.to_csv(out / "fused_demand_matrix.csv", index=False)

    metrics, aligned = validate(fused, oracle)
    aligned.to_csv(out / "layer2_validation_detail.csv", index=False)

    print("-" * 70)
    print("VALIDATION vs ground-truth oracle:")
    for k, v in metrics.items():
        print(f"   {k:<22}: {v}")
    print("-" * 70)
    print("Interpretation:")
    print(f"   boarding margins (ATTO-pinned) error    : {metrics['boarding_mae']} riders/cell  (easy part)")
    print(f"   alighting distribution similarity       : {metrics['alight_cosine_wtd']}  (hard part — the fusion's job)")
    print(f"   top-20 OD recall                        : {metrics['top20_od_recall']}")
    print("-" * 70)
    print(f"Written: fused_demand_matrix.csv, layer2_validation_detail.csv")
    print(f"Elapsed: {time.time()-t0:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
