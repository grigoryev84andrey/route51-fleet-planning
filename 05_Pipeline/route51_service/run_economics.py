"""Economic evaluation with explicit scope (thesis §3.5, schema 2).

Reads a layer-3 run (result object and executable dispatches) and, optionally, a
simulation analysis; writes economics_result.json and a flat economics.csv.
All formulas live in layer5/economics.py; resources in layer3/resources.py.

Usage:
  python run_economics.py --output-dir DIR --input-dir <layer-3 run dir>
        --input fused_demand_matrix.csv=PATH [--input simulation_analysis.json=PATH
        --protocol protocol.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import result_contracts as rc  # noqa: E402
import runctx  # noqa: E402
from layer3 import provenance  # noqa: E402
from layer3.dispatch import dispatch_metrics, trips_from_frame  # noqa: E402
from layer3.resources import resources  # noqa: E402
from layer3.study import setup  # noqa: E402
from layer5 import economics as eco  # noqa: E402


def main() -> None:
    ctx = runctx.start(__file__, inputs=["layer3_result.json", "dispatch_baseline.csv",
                                         "dispatch_candidate.csv", "fused_demand_matrix.csv"],
                       optional_inputs=["simulation_analysis.json"])
    cfg, out = ctx.cfg, ctx.out
    l3 = json.loads(ctx.input("layer3_result.json").read_text(encoding="utf-8"))
    if l3.get("schema_version") != rc.SCHEMA_VERSION:
        raise SystemExit("layer3_result.json is not schema 2")
    sim = (json.loads(ctx.inputs["simulation_analysis.json"].read_text(encoding="utf-8"))
           if "simulation_analysis.json" in ctx.inputs else None)
    if sim is not None and ctx.args.protocol is None:
        raise SystemExit("--protocol is required when a simulation result is used")

    fused = pd.read_csv(ctx.input("fused_demand_matrix.csv"))
    st = setup(cfg, fused)
    trips = {"baseline": trips_from_frame(pd.read_csv(ctx.input("dispatch_baseline.csv"))),
             "candidate": trips_from_frame(pd.read_csv(ctx.input("dispatch_candidate.csv")))}
    fps = {"input": provenance.input_fp(fused, cfg),
           "plan_baseline": provenance.plan_fp(trips["baseline"]),
           "plan_candidate": provenance.plan_fp(trips["candidate"])}
    for k in ("input", "plan_baseline", "plan_candidate"):
        if fps[k] != l3["fingerprints"][k]:
            raise SystemExit(f"{k} fingerprint differs from the layer-3 result")
    if sim is not None:
        protocol = json.loads(ctx.args.protocol.read_text(encoding="utf-8"))
        series = protocol["experiment"]["confirmatory_series"]
        fps.update(model=provenance.model_fp(), protocol=rc.protocol_fingerprint(protocol),
                   counts={"baseline": len(series["baseline_seeds"]),
                           "candidate": len(series["candidate_seeds"])})
        if l3["fingerprints"].get("model") != fps["model"]:
            raise SystemExit("model fingerprint differs from the layer-3 result")

    op = cfg["operations"]
    m = {c: dispatch_metrics(t, st.summary, st.params) for c, t in trips.items()}
    res_all = {c: resources(t, st.params, one_way_min=op["one_way_min"], layover_min=op["layover_min"])
               for c, t in trips.items()}
    res_am = {c: resources(t, st.params, one_way_min=op["one_way_min"], layover_min=op["layover_min"],
                           periods=["am_peak"]) for c, t in trips.items()}
    rate = op.get("cost_per_veh_km_uzs_12m")

    ivt, ivt_reason, verdict = eco.simulation_ivt(sim, fps)
    am = eco.scope_block(
        scope="am_peak",
        wait_base=m["baseline"]["by_period"]["am_peak"]["wait"],
        wait_plan=m["candidate"]["by_period"]["am_peak"]["wait"],
        crowd_base=m["baseline"]["by_period"]["am_peak"]["crowd_min"],
        crowd_plan=m["candidate"]["by_period"]["am_peak"]["crowd_min"],
        K_base=res_am["baseline"]["equiv_12m_km"], K_plan=res_am["candidate"]["equiv_12m_km"],
        ivt_benefit_pax_min=ivt, ivt_reason=ivt_reason, rate_12m=rate, fingerprints=fps,
        evidence_kind="model_only" if ivt is None else "model_plus_simulation_derived")
    day = eco.scope_block(
        scope="all_day_model",
        wait_base=m["baseline"]["wait"], wait_plan=m["candidate"]["wait"],
        crowd_base=m["baseline"]["crowd_min"], crowd_plan=m["candidate"]["crowd_min"],
        K_base=res_all["baseline"]["equiv_12m_km"], K_plan=res_all["candidate"]["equiv_12m_km"],
        ivt_benefit_pax_min=None, ivt_reason="not_carried_over_from_morning_simulation",
        rate_12m=rate, fingerprints=fps)
    result = {
        "schema_version": rc.SCHEMA_VERSION,
        "economics_version": eco.ECONOMICS_VERSION,
        "run_id": ctx.run_id,
        "layer3_run_id": l3.get("run_id"),
        "simulation_run_id": (sim or {}).get("run_id"),
        "simulation_verdict": verdict,
        "value_of_time": eco.VOT_BASIS,
        "days_per_year": {"value": eco.DAYS_PER_YEAR, "evidence_type": "scenario_assumption"},
        "operator_rate": {"value": rate, "status": "known" if rate is not None else "unknown",
                          "note": "null = no sourced rate; zero would be a different statement"},
        "resources": {"all_day": res_all, "am_peak": res_am},
        "scopes": {"am_peak": am, "all_day_model": day,
                   "all_day_with_simulation": eco.not_evaluated(
                       "all_day_with_simulation", "not_evaluated",
                       "Only the morning cohort is simulated; no comparable all-period simulation.")},
        "fingerprints": fps,
    }
    (out / "economics_result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                               encoding="utf-8")
    rows = []
    for sc in ("am_peak", "all_day_model"):
        for w, b in result["scopes"][sc]["by_wait_weight"].items():
            for k, v in b.items():
                if not isinstance(v, dict):
                    rows.append({"scope": sc, "wait_weight": w, "indicator": k, "value": v})
    for sc, rr in (("all_day", res_all), ("am_peak", res_am)):
        for c, d in rr.items():
            for k, v in d.items():
                if not isinstance(v, list):
                    rows.append({"scope": f"resources_{sc}", "wait_weight": c, "indicator": k, "value": v})
    pd.DataFrame(rows).to_csv(out / "economics.csv", index=False)

    print("=" * 78)
    print("ECONOMIC EVALUATION (scoped)")
    print("=" * 78)
    print(f"Value of time: {eco.VALUE_OF_TIME_UZS_H:,.0f} UZS per pax-hour; days {eco.DAYS_PER_YEAR}; "
          f"operator rate: {rate}")
    print(f"Simulation applicable: {verdict['applicable']}  (IVT: {ivt} / {ivt_reason})")
    for sc in ("am_peak", "all_day_model"):
        b = result["scopes"][sc]["by_wait_weight"]["wait_weight_1"]
        print(f"  [{sc}] wait {b['wait_benefit_h']:,.0f} h/yr, crowding {b['crowd_benefit_h']:,.0f} h/yr, "
              f"model {b['passenger_model_uzs']/1e6:,.0f} mn UZS/yr, "
              f"dK {b['delta_equiv_12m_km_year']:,.0f} equiv-km/yr, threshold {b['rate_threshold_model_only']}")
    print(f"Written: economics_result.json, economics.csv -> {out}")


if __name__ == "__main__":
    main()
