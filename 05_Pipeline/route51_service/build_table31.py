"""Assemble Table 3.1 — baseline KPIs (thesis §3.3.1), schema 2.

Every figure is read from the artefact that produced it:

  simulated KPIs  <- simulation_analysis.json (registered series; baseline condition)
  model KPIs      <- layer3_result.json      (operations model on the executable timetable)
  coverage (H2)   <- coverage_h2.csv          (deterministic, run_coverage.py)

The Bottleneck Index (H3) is not instrumented and is emitted as an explicit
"deferred" row. The baseline timetable's headways are a scenario input (the
"March 2024 GTFS" label could not be verified against a feed).

Usage: python build_table31.py --output-dir DIR --input layer3_result.json=PATH
         --input simulation_analysis.json=PATH --input coverage_h2.csv=PATH
Outputs (run directory): table3_1_baseline_kpis.csv, table3_1_baseline_kpis.md
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

import runctx  # noqa: E402

DEFERRED = "not instrumented in this run — deferred (§2.6.3)"


def _row(kpi, unit, value, source, maps_to, dispersion="—", ci="—", n="—"):
    return {"kpi": kpi, "unit": unit, "value": value, "dispersion": dispersion, "ci95": ci, "n": n,
            "source": source, "maps_to": maps_to}


def main() -> None:
    ctx = runctx.start(__file__, inputs=["layer3_result.json", "simulation_analysis.json",
                                         "coverage_h2.csv"])
    out = ctx.out
    l3 = json.loads(ctx.input("layer3_result.json").read_text(encoding="utf-8"))
    sim = json.loads(ctx.input("simulation_analysis.json").read_text(encoding="utf-8"))
    cov = pd.read_csv(ctx.input("coverage_h2.csv")).set_index("reference_area")
    if l3.get("schema_version") != 2 or sim.get("schema_version") != 2:
        raise SystemExit("inputs are not schema 2")
    bm = l3["baseline"]["dispatch_metrics"]
    p = sim["primary"]
    n = p["n_baseline"]
    half = stats.t.ppf(0.975, n - 1) * p["baseline_sd"] / math.sqrt(n)
    series = f"SUMO, {n} runs, series '{sim['series']}' ({sim['evidence_kind']})"
    rows = [
        _row("Mean full-trip bus duration, 07:00–10:00 departures, both directions", "min",
             f"{p['baseline_mean']:.2f}", series, "H1_bus_v2 (primary outcome)",
             f"SD {p['baseline_sd']:.3f}",
             f"[{p['baseline_mean'] - half:.2f}, {p['baseline_mean'] + half:.2f}]", n),
        _row("Waiting", "pax·min/day", f"{bm['wait']:,.0f}", "Layer-3 model, executable timetable",
             "objective component"),
        _row("Crowding disutility (ramp, θ 0.80, λ(1) 2.0)", "pax·min/day", f"{bm['crowd_min']:,.0f}",
             "Layer-3 model, executable timetable", "objective component"),
        _row("Waiting + crowding disutility", "pax·min/day", f"{bm['gen_cost']:,.0f}",
             "Layer-3 model, executable timetable", "selection criterion"),
        _row("Operating-cost index", "relative veh-km", f"{bm['cost']:,.1f}",
             "Layer-3 model, executable timetable", "cost constraint"),
        _row("Maximum operating-cost index (cap, ×1.25)", "relative veh-km",
             f"{l3['parameters']['budget']:,.1f}", "Layer-3 model", "cost constraint (§3.6.b)"),
        _row("Headway spread across periods, (max − min) / mean", "—", f"{bm['headway_spread']:.3f}",
             "Layer-3 model", "spread constraint (§3.6.a); not spatial equity"),
        _row("Highest load / full capacity", "—", f"{bm['max_phi']:.2f}", "Layer-3 model",
             "above 1: exceeds full capacity (queues not modelled)"),
        _row("Vehicles needed (normative)", "vehicles", f"{bm['fleet_needed']}", "Layer-3 model",
             "normative fleet check; availability not verified"),
        _row("500 m coverage ratio, corridor band (1 km of the alignment)", "%",
             f"{cov.loc['corridor band (1 km of the alignment)', 'coverage_pct']:.1f}",
             "WorldPop 100 m + OSM boundaries", "H2 (deterministic; identical under both plans)"),
        _row("500 m coverage ratio, corridor districts", "%",
             f"{cov.loc['corridor districts', 'coverage_pct']:.1f}",
             "WorldPop 100 m + OSM boundaries", "H2 (reach into the districts served)"),
        _row("Bottleneck Index", "—", DEFERRED, "requires per-segment occupancy tracking", "H3 (deferred)"),
    ]
    pd.DataFrame(rows).to_csv(out / "table3_1_baseline_kpis.csv", index=False)
    md = ["| KPI | Unit | Baseline | Dispersion | 95% CI | n | Source |", "|---|---|---|---|---|---|---|"]
    md += [f"| {r['kpi']} | {r['unit']} | {r['value']} | {r['dispersion']} | {r['ci95']} | {r['n']} | "
           f"{r['source']} |" for r in rows]
    (out / "table3_1_baseline_kpis.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
