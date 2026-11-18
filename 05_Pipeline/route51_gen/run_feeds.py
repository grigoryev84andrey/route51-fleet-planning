"""Feed 1 (telecom CDR) + Feed 2 (ATTO) from the Stage 1-2 substrate."""
from __future__ import annotations

import sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from config import load_config  # noqa: E402
from geo import build_corridor  # noqa: E402
from feed_atto import generate_atto  # noqa: E402
from feed_telecom import generate_telecom  # noqa: E402


def main() -> None:
    t0 = time.time()
    cfg = load_config(ROOT / "config.yaml")
    rng = np.random.default_rng(cfg.run.random_seed)
    sa = cfg.study_area
    out = Path(cfg.run.output_dir)

    persons = pd.read_parquet(out / "persons.parquet")
    trips = pd.read_parquet(out / "trips.parquet")
    corr = build_corridor(
        stops_csv=f"{cfg.run.data_dir}/{sa.stops_csv}", res=sa.h3_resolution,
        buffer_m=sa.catchment_buffer_m, anchors=cfg["attraction_anchors"],
        cordon_zones=sa["cordon_zones"], density_model=cfg["population"]["home_density_model"])

    atto = generate_atto(cfg, persons, trips, rng)
    tel = generate_telecom(cfg, persons, trips, corr, rng)

    atto.to_csv(out / "atto_taps_raw.csv", index=False)
    tel.to_parquet(out / "telecom_cdr_raw.parquet", index=False)
    tel.to_csv(out / "telecom_cdr_raw.csv", index=False)

    print("=" * 70)
    print("FEED 1 (telecom CDR) + FEED 2 (ATTO)")
    print("=" * 70)
    bus_trips = int(trips["route51"].sum())
    print(f"ATTO taps (Feed 2)        : {len(atto):,}  from {bus_trips:,} bus legs "
          f"({len(atto)/max(bus_trips,1)*100:.1f}% tapped; rest = inactive+evasion)")
    print(f"  distinct hashed cards   : {atto.hashed_card_id.nunique():,}")
    print(f"  card-type mix           : {dict(atto.card_type.value_counts())}")
    print(f"  alighting field present : {'alight' in atto.columns or 'tap_out' in atto.columns}  "
          f"(must be False)")
    print(f"  sample id (must be hash): {atto.hashed_card_id.iloc[0][:20]}... "
          f"len={len(atto.hashed_card_id.iloc[0])}")
    print("-" * 70)
    obs_n = int(((persons.phone_owner) & (persons.operator == cfg['telecom']['observed_operator'])).sum())
    print(f"Telecom CDR (Feed 1)      : {len(tel):,} events  "
          f"from {obs_n:,} observed-operator subscribers "
          f"({len(tel)/max(obs_n,1):.0f} events/subscriber)")
    print(f"  event-type mix          : {dict(tel.event_type.value_counts())}")
    print(f"  distinct serving cells  : {tel.h3_cell.nunique():,}")
    print(f"  localization tag        : {sorted(tel.processing_region.unique())}")
    print(f"  raw coords present      : {('lat' in tel.columns or 'lon' in tel.columns)}  (must be False)")
    print(f"  sample id (must be hash): {tel.hashed_subscriber_id.iloc[0][:20]}... "
          f"len={len(tel.hashed_subscriber_id.iloc[0])}")
    print("-" * 70)
    print(f"Written: atto_taps_raw.csv, telecom_cdr_raw.csv (+ .parquet)")
    print(f"Elapsed: {time.time()-t0:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
