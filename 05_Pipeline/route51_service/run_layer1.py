"""Run Layer 1 on the available Layer-0 feeds; write outputs + governance report."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from layer1.contracts import CONTRACTS
import runctx  # noqa: E402
from layer1.gateway import build_gateway

ROOT = Path(__file__).resolve().parent


def _load_feed(cfg, key):
    p = Path(cfg["_root"]) / cfg["paths"]["layer0_dir"] / cfg["paths"][key]
    return pd.read_csv(p) if p.exists() else None


def main() -> None:
    ctx = runctx.start(__file__)
    gw, cfg = build_gateway(ctx.args.config.resolve())
    out = ctx.out
    api_key = cfg["service"]["api_key"]

    print("=" * 70)
    print("LAYER 1 — Data ingestion & governance")
    print("=" * 70)
    print(f"Reference: {len(gw.ref.stops)} stops, {len(gw.ref.grid)} H3 grid cells, "
          f"fleet {gw.ref.fleet}")
    print("Registered feed contracts (all enforced):")
    for name, c in CONTRACTS.items():
        print(f"  - {name:<8} {c.privacy_class.value:<34} "
              f"localization={'yes' if c.localization_required else 'no '}")
    print("-" * 70)

    report = ["# Layer 1 — governance report\n"]
    for feed_key, file_key in (("gps", "gps_file"), ("atto", "atto_file"),
                               ("telecom", "telecom_file")):
        df = _load_feed(cfg, file_key)
        if df is None:
            print(f"[{feed_key}] no Layer-0 data yet (contract registered, awaiting feed)")
            report.append(f"## {feed_key}\n- status: **awaiting data** "
                          f"(contract registered & enforced)\n")
            continue

        res = gw.ingest(feed_key, df, api_key=api_key)
        status = "ACCEPTED" if res.accepted else "QUARANTINED"
        print(f"[{feed_key}] {status}: in={res.n_in} valid={res.n_valid}")
        report.append(f"## {feed_key}\n- decision: **{status}**\n"
                      f"- records in / valid: {res.n_in} / {res.n_valid}\n")

        if res.schema_errors:
            print("   schema:", res.schema_errors)
            report.append(f"- schema errors: {res.schema_errors}\n")
        if res.governance:
            g = res.governance
            print(f"   governance: {'PASS' if g.passed else 'FAIL'} "
                  f"(class={g.stats.get('privacy_class')})")
            for v in g.violations:
                print("     violation:", v)
            for n in g.notes:
                print("     note:", n)
            report.append(f"- governance: **{'PASS' if g.passed else 'FAIL'}** "
                          f"(class {g.stats.get('privacy_class')})\n")
            for v in g.violations:
                report.append(f"  - violation: {v}\n")
            for n in g.notes:
                report.append(f"  - note: {n}\n")
        if res.index:
            s = res.index.stats
            print(f"   indexing: {s['records_indexed']} indexed, "
                  f"{s['cells_touched']} cells, in_grid {s['in_grid_share']*100:.1f}%, "
                  f"k-anon suppressed {s['cells_suppressed']} "
                  f"(applies={s['suppression_applies']})")
            report.append(f"- indexing: {s['records_indexed']} records, "
                          f"{s['cells_touched']} cells, in-grid {s['in_grid_share']*100:.1f}%, "
                          f"k-anon (k={s['k_anonymity']}) suppressed {s['cells_suppressed']} "
                          f"cells (applies={s['suppression_applies']})\n")
            res.index.indexed.to_parquet(out / f"{feed_key}_indexed.parquet", index=False)
            res.index.aggregate.to_csv(out / f"{feed_key}_aggregate.csv", index=False)
            print(f"   written: {feed_key}_indexed.parquet, {feed_key}_aggregate.csv")

    (out / "governance_report.md").write_text("".join(report), encoding="utf-8")
    print("-" * 70)
    print(f"Governance report: {out/'governance_report.md'}")
    print("=" * 70)


if __name__ == "__main__":
    main()
