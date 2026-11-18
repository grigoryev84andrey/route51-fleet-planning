"""Registered simulation series (protocol route51_v2 v1.0).

Simulates the executable timetables written by run_layer3.py — the baseline and
the selected candidate — in both directions under the protocol's seeds, checks
every run for a complete cohort, and applies the registered analysis.

Usage:
    python run_replications.py --output-dir DIR --protocol protocol.json \\
        --input-dir <layer-3 run dir> --input fused_demand_matrix.csv=PATH \\
        --series technical|confirmatory|sensitivity [--speedup 0.30] [--workers 6]

Series and seeds come from the protocol: technical 3+3 (exploratory),
confirmatory 30+30, sensitivity = candidate only with the confirmatory candidate
seeds and a different boarding speed-up (the baseline has no 18 m buses, so its
condition is identical and is not re-run).

Outputs (run directory): runs.csv (one row per run), buses.csv (one row per cohort
bus), simulation_analysis.json, sim/<tag>/ (SUMO inputs, compressed outputs, logs).
"""
from __future__ import annotations

import gzip
import json
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import result_contracts as rc  # noqa: E402
import runctx  # noqa: E402
from layer1.reference import load_reference  # noqa: E402
from layer3 import provenance  # noqa: E402
from layer3.dispatch import trips_from_frame  # noqa: E402
from layer3.operations import canonical_stop_ids  # noqa: E402
from layer4.analysis import analyse  # noqa: E402
from layer4.background import BackgroundError, generate_background, sumo_home  # noqa: E402
from layer4.experiment import (EXPERIMENT_VERSION, GUARD_END_S, SIM_BEGIN_S, RunInvalid,  # noqa: E402
                               am_od, parse_run, run_outcome, run_sumo, stop_activity,
                               write_bus_routes)
from layer4.network import build_network_bidir  # noqa: E402


def _one_run(job: dict) -> dict:
    """Worker: one condition x one seed, in its own directory."""
    wd = Path(job["run_dir"])
    wd.mkdir(parents=True, exist_ok=False)
    tag, seed = job["tag"], job["seed"]
    t0 = time.time()
    rec = {"condition": job["condition"], "seed": seed, "tag": tag, "speedup": job["speedup"]}
    try:
        cars = generate_background(job["net"], wd, seed, SIM_BEGIN_S, GUARD_END_S,
                                   job["traffic_period_s"], strict=True)
        ids = write_bus_routes(job["trips"], job["activity"], job["edges"], job["bstops"],
                               job["sim"], job["speedup"], seed, wd / "buses.rou.xml")
        run_sumo(job["net"], job["add"], wd, [wd / "buses.rou.xml", cars], tag, seed,
                 job["teleport_s"])
        parsed = parse_run(wd, tag, ids)
        out = run_outcome(parsed, job["bstops"], job["od_am"])
        rec.update({"valid": parsed["valid"], "problems": parsed["problems"],
                    **parsed["id_sets"], "background_teleports": parsed["background_teleports"],
                    **{k: v for k, v in out.items() if k != "valid"}})
        rec["_buses"] = [{**b, "condition": job["condition"], "seed": seed} for b in parsed["buses"]]
        # Keep compressed outputs and logs. Background intermediates are deterministic
        # in the seed and are dropped; the routed cars file is kept compressed.
        for p in list(wd.glob("trips_*.xml")) + list(wd.glob("*.rou.alt.xml")) + [wd / "routes.rou.xml"]:
            if p.exists():
                p.unlink()
        if cars is not None and Path(cars).exists():
            with open(cars, "rb") as src, gzip.open(str(cars) + ".gz", "wb") as dst:
                shutil.copyfileobj(src, dst)
            Path(cars).unlink()
    except (RunInvalid, BackgroundError) as e:
        rec.update({"valid": False, "problems": [{"code": type(e).__name__, "message": str(e)}],
                    "primary_min": None})
        rec["_buses"] = []
    rec["seconds"] = time.time() - t0
    return rec


def main():
    def extra(ap):
        ap.add_argument("--series", required=True, choices=["technical", "confirmatory", "sensitivity"])
        ap.add_argument("--speedup", type=float, default=None,
                        help="boarding speed-up of 18 m buses (default: protocol central)")
        ap.add_argument("--workers", type=int, default=6)

    ctx = runctx.start(__file__, inputs=["fused_demand_matrix.csv", "dispatch_baseline.csv",
                                         "dispatch_candidate.csv", "layer3_result.json"],
                       add_args=extra, require_protocol=True)
    args, cfg, out = ctx.args, ctx.cfg, ctx.out
    protocol = json.loads((out / "protocol.json").read_text(encoding="utf-8"))
    l3 = json.loads(ctx.input("layer3_result.json").read_text(encoding="utf-8"))
    if l3.get("schema_version") != rc.SCHEMA_VERSION:
        raise SystemExit("layer3_result.json is not schema 2")
    if sumo_home() is None:
        raise SystemExit("SUMO_HOME/tools not found: background traffic is required by the protocol")

    sp = cfg["simulation"]
    central = float(protocol["simulation"]["boarding_speedup_18m"]["central"])
    speedup = central if args.speedup is None else float(args.speedup)
    allowed = protocol["simulation"]["boarding_speedup_18m"]["sensitivity"]
    if speedup not in allowed:
        raise SystemExit(f"speed-up {speedup} is not in the protocol set {allowed}")
    sim = {"dwell_base_s": sp["dwell_base_s"], "dwell_per_pax_s": sp["dwell_per_pax_s"]}
    teleport_s = int(protocol["simulation"]["time_to_teleport_s"])
    ex = protocol["experiment"]
    if args.series == "technical":
        seeds = {"baseline": ex["technical_series_K1"]["baseline_seeds"],
                 "candidate": ex["technical_series_K1"]["candidate_seeds"]}
        evidence = "exploratory"
    elif args.series == "confirmatory":
        if speedup != central:
            raise SystemExit("the confirmatory series uses the central speed-up only")
        seeds = {"baseline": ex["confirmatory_series"]["baseline_seeds"],
                 "candidate": ex["confirmatory_series"]["candidate_seeds"]}
        evidence = "confirmatory"
    else:
        if speedup == central:
            raise SystemExit("a sensitivity series needs a non-central speed-up")
        seeds = {"candidate": ex["confirmatory_series"]["candidate_seeds"]}
        evidence = "exploratory"

    ref = load_reference(cfg)
    stop_ids = canonical_stop_ids(ref)
    fused = pd.read_csv(ctx.input("fused_demand_matrix.csv"))
    fused_scaled = fused.copy()
    fused_scaled["demand"] = fused_scaled["demand"] * float(cfg["operations"]["demand_scale"])
    activity = stop_activity(fused_scaled, stop_ids)
    od = am_od(fused_scaled, stop_ids)

    trips = {"baseline": trips_from_frame(pd.read_csv(ctx.input("dispatch_baseline.csv"))),
             "candidate": trips_from_frame(pd.read_csv(ctx.input("dispatch_candidate.csv")))}
    fps = {c: provenance.plan_fp(t) for c, t in trips.items()}
    for c, key in (("baseline", "plan_baseline"), ("candidate", "plan_candidate")):
        if fps[c] != l3["fingerprints"][key]:
            raise SystemExit(f"{c} dispatch does not match the layer-3 result fingerprint")
    in_fp = provenance.input_fp(fused, cfg)
    if in_fp != l3["fingerprints"]["input"]:
        raise SystemExit("input data/configuration differ from the layer-3 run")

    net_dir = out / "network"
    net, edges, bstops = build_network_bidir(stop_ids, ref.stops, sp["link_speed_kmh"], net_dir,
                                             lanes=int(sp["replications"]["traffic_lanes"]))
    jobs = []
    for cond, sl in seeds.items():
        for s in sl:
            tag = f"{cond}_{s}"
            jobs.append({"condition": cond, "seed": int(s), "tag": tag, "speedup": speedup,
                         "run_dir": str(out / "sim" / tag), "net": net, "add": net_dir / "stops.add.xml",
                         "edges": edges, "bstops": bstops, "trips": trips[cond], "activity": activity,
                         "od_am": od, "sim": sim, "teleport_s": teleport_s,
                         "traffic_period_s": int(sp["replications"]["traffic_period_s"])})
    print("=" * 78)
    print(f"SIMULATION SERIES '{args.series}' — {len(jobs)} runs, speed-up {speedup}, "
          f"workers {args.workers}, protocol {protocol['protocol_version']}")
    print("=" * 78)
    t0 = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(_one_run, j) for j in jobs]
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            print(f"  {r['tag']:<22} valid={r['valid']}  primary={r.get('primary_min')}  "
                  f"({r['seconds']:.0f}s)  [{len(results)}/{len(jobs)}]", flush=True)
    wall = time.time() - t0
    results.sort(key=lambda r: (r["condition"], r["seed"]))
    buses = [b for r in results for b in r.pop("_buses")]
    pd.DataFrame(buses).to_csv(out / "buses.csv", index=False)
    pd.DataFrame([{k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in r.items()}
                  for r in results]).to_csv(out / "runs.csv", index=False)

    if args.series == "sensitivity":
        analysis = {"series": "sensitivity", "evidence_kind": "exploratory",
                    "note": "candidate only; compare with the confirmatory baseline runs of the "
                            "same protocol (identical baseline condition)",
                    "candidate_runs_valid": all(r["valid"] for r in results),
                    "candidate_primary_mean": (sum(r["primary_min"] for r in results if r["valid"]) /
                                               max(1, sum(1 for r in results if r["valid"])))}
    else:
        analysis = analyse(results, series=args.series, protocol_passed=True, evidence_kind=evidence)
    analysis.update({
        "schema_version": rc.SCHEMA_VERSION,
        "experiment_version": EXPERIMENT_VERSION,
        "run_id": ctx.run_id,
        "protocol": {"id": protocol["protocol_id"], "version": protocol["protocol_version"],
                     "sha256": ctx._meta["protocol"]["sha256"]},
        "fingerprints": {"input": in_fp, "plan_baseline": fps["baseline"],
                         "plan_candidate": fps["candidate"], "model": provenance.model_fp(),
                         "protocol": rc.protocol_fingerprint(protocol)},
        "layer3_run_id": l3.get("run_id"),
        "candidate_id": (l3.get("candidate") or {}).get("cid"),
        "boarding_speedup_18m": speedup,
        "seeds": seeds,
        "wall_seconds": wall,
        "mean_run_seconds": sum(r["seconds"] for r in results) / max(1, len(results)),
        "background_teleports_total": int(sum(r.get("background_teleports") or 0 for r in results)),
        "operational_feasibility": "unknown",
        "limitations": [
            "Primary outcome is bus full-trip duration, not individual passenger in-vehicle time.",
            "Queues at stops, denied boarding and signal priority are not modelled.",
            "Only the 07:00-10:00 departure cohort is measured; other periods are not simulated.",
        ],
    })
    (out / "simulation_analysis.json").write_text(json.dumps(analysis, indent=2, ensure_ascii=False,
                                                             default=float), encoding="utf-8")
    print("-" * 78)
    if "primary" in analysis:
        p = analysis["primary"]
        print(f"completion={analysis['completion_status']}  protocol={analysis['protocol_status']}  "
              f"effect={analysis['effect_status']}")
        if p.get("defined"):
            print(f"baseline {p['baseline_mean']:.3f}  candidate {p['candidate_mean']:.3f}  "
                  f"delta {p['delta_min']:+.3f} min  95% CI [{p['ci95'][0]:+.3f}, {p['ci95'][1]:+.3f}]  "
                  f"p={p['p']:.3g}")
    print(f"wall {wall:.0f}s; mean run {analysis['mean_run_seconds']:.1f}s -> {out}")


if __name__ == "__main__":
    main()
