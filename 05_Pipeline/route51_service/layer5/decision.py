"""Layer 5 — decision record (schema 2).

Consolidates already-computed results into one structured package. Nothing is
recomputed here: every number is read from the layer-3 result object
(`layer3_result.json`), and, when present, the simulation analysis
(`simulation_analysis.json`) and the economics result (`economics_result.json`).
Statuses come from those objects; a missing input stays missing (None with a
reason), never a default.
"""
from __future__ import annotations

import json
from pathlib import Path

import result_contracts as rc


def _load_json(path: Path | None):
    if path is None or not Path(path).exists():
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _gov_summary(path: Path | None):
    if path is None or not Path(path).exists():
        return {"present": False}
    txt = Path(path).read_text(encoding="utf-8")
    feeds = [ln[3:].strip() for ln in txt.splitlines() if ln.startswith("## ")]
    return {"present": True, "feeds": feeds,
            "all_accepted": txt.count("**ACCEPTED**") >= len(feeds) and len(feeds) > 0,
            "all_pass": "**FAIL**" not in txt}


def build_record(layer3: dict, *, simulation: dict | None = None,
                 economics: dict | None = None, governance_report: Path | None = None) -> dict:
    if layer3.get("schema_version") != rc.SCHEMA_VERSION:
        raise ValueError("layer-3 result is not schema 2")
    p = layer3["parameters"]
    base = layer3["baseline"]
    cand = layer3.get("candidate") or {}
    has_cand = bool(cand.get("cid"))

    rec: dict = {
        "schema_version": rc.SCHEMA_VERSION,
        "layer3_run_id": layer3.get("run_id"),
        "fingerprints": layer3.get("fingerprints"),
        "model_versions": layer3.get("model_versions"),
        "context": {
            "route": "51",
            "demand_scenario_scale": p["demand_scale"],
            "default_headway_spread_cap": p["delta"],
            "crowding": p["crowding"],
            "evidence": "synthetic model; no operational data",
        },
        "operational_feasibility": "unknown",
    }

    rows = []
    for k, per in enumerate(("am_peak", "midday", "pm_peak", "off_peak")):
        row = {"period": per, "baseline_headway_min": base["headways"][k]}
        if has_cand:
            act = {(r["period"], r["direction"]): r for r in cand["dispatch_metrics"]["by_period_direction"]}
            a = act[(per, "fwd")]
            row.update({"plan_headway_min": cand["headways"][k],
                        "plan_effective_headway_min": cand["dispatch_metrics"]["effective_headways"][per],
                        "plan_share_18m_desired": cand["shares"][k],
                        "plan_trips_per_direction": a["trips"],
                        "plan_trips_18m_per_direction": a["trips_18m"]})
        rows.append(row)
    rec["plan_table"] = rows

    bm = base["dispatch_metrics"]
    rec["model_comparison"] = {
        "basis": "layer-3 operations model on the executable dispatch (not simulation)",
        "evidence_kind": "model_only",
        "unit": "pax·min/day (wait, crowd_min); index (cost)",
        "baseline": {k: bm[k] for k in ("wait", "crowd_min", "gen_cost", "cost", "max_phi",
                                         "headway_spread", "fleet_needed")},
        "candidate": ({k: cand["dispatch_metrics"][k] for k in ("wait", "crowd_min", "gen_cost", "cost",
                                                                 "max_phi", "headway_spread", "fleet_needed")}
                      if has_cand else rc.unknown("no_feasible_candidate",
                                                  "No executable candidate was feasible after dispatch.")),
        "candidate_id": cand.get("cid"),
    }

    front = layer3.get("headway_spread_frontier") or []
    opts = []
    for r in front:
        if not r.get("feasible"):
            opts.append({"delta": r["delta"], "feasible": False})
            continue
        opts.append({"delta": None if r["delta"] >= 999 else r["delta"], "feasible": True,
                     "gen_cost_relaxed": r["gen_cost"], "wait_relaxed": r["wait"],
                     "crowd_min_relaxed": r["crowd_min"], "cost_relaxed": r["cost"]})
    rec["headway_spread_options"] = {
        "basis": "relaxed optimum per cap, seed 42 (exploratory; not re-checked on dispatch)",
        "baseline_gen_cost_relaxed": base["relaxed_metrics"]["gen_cost"],
        "options": opts,
    }

    rec["simulation"] = simulation if simulation is not None else rc.unknown(
        "no_result", "No simulation analysis is attached to this record.")
    rec["economics"] = economics if economics is not None else rc.unknown(
        "no_result", "No economics result is attached to this record.")
    rec["governance"] = _gov_summary(governance_report)
    rec["limitations"] = [
        "Synthetic methods demonstration — not an empirical claim about real Line 51.",
        f"Demand scenario ×{p['demand_scale']} is a labelled assumption.",
        "Crowding parameters (theta, lambda) are scenario assumptions, not local measurements.",
        "Every trip of a period is modelled as an average vehicle; queues and denied boarding are not modelled.",
        "Fleet check is normative (departures per cycle), not a vehicle-block schedule; "
        "operational feasibility is unknown.",
        "The headway-spread cap limits variation between periods; it is not a spatial-equity measure.",
    ]
    return rec


def load_inputs(out: Path) -> dict:
    return {
        "layer3": _load_json(out / "layer3_result.json"),
        "simulation": _load_json(out / "simulation_analysis.json"),
        "economics": _load_json(out / "economics_result.json"),
    }
