"""The bridge from the console to the shared Route-51 computation.

Every scientific number comes from the modules in `Final/05_Pipeline/route51_service`
— the same code the thesis scripts run:

  data        layer0 generator + layer1 gateway + layer2 fusion, `validate()` v2
  plans       layer3.study (protocol selection over three seeds, dispatch check)
  simulation  layer4.experiment / layer4.analysis via run_replications workers
  economics   layer5.economics (scoped), layer3.resources
  statuses    result_contracts (one validator)

Nothing here re-implements a formula. The console shows what these return.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

import archive
from settings import (BUDGET_STOPS, DELTA_STOPS, GEN_CONFIG, GEN_DIR, ORACLE, PRESET_BY_ID,
                      QUALITY_STOPS, SERVICE_CONFIG, THESIS_MATRIX, install_import_paths,
                      protocol_path)

install_import_paths()

import result_contracts as rc                                  # noqa: E402
import runctx                                                  # noqa: E402
from layer1.gateway import build_gateway                       # noqa: E402
from layer2.alight_inference import chain_alightings           # noqa: E402
from layer2.downscaling import fuse                            # noqa: E402
from layer2.telecom_od import build_telecom_od                 # noqa: E402
from layer2.validation import VALIDATION_VERSION, validate     # noqa: E402
from layer3 import provenance                                  # noqa: E402
from layer3.dispatch import feasibility, select_executable     # noqa: E402
from layer3.operations import PERIOD_NAMES, PERIODS            # noqa: E402
from layer3.optimize import rank_candidates                    # noqa: E402
from layer3.resources import resources                         # noqa: E402
from layer3.study import optimise_one_seed, select_from_candidates, setup   # noqa: E402
from layer4.analysis import analyse                            # noqa: E402
from layer4.experiment import am_od, stop_activity             # noqa: E402
from layer4.network import build_network_bidir                 # noqa: E402
from layer5 import economics as eco                            # noqa: E402

Progress = Callable[..., None]

PERIOD_LABELS = {"am_peak": "Morning peak 07–10", "midday": "Midday 10–16",
                 "pm_peak": "Evening peak 16–19", "off_peak": "Off-peak 05–07, 19–23"}
ALT_TITLES = {"recommended": "Recommended for the selected priorities",
              "least_waiting": "Least waiting",
              "least_crowding": "Least crowding"}

STATUS_LABELS = {
    "historical": "Historical run — validation issues remain",
    "not_simulated": "Not simulated for this plan",
    "different": "Reference only — different scenario",
    "technical": "Technical run — not confirmatory evidence",
    "evaluated": "Evaluated under the registered simulation protocol",
    "incomplete": "Incomplete run — no confirmatory conclusion",
    "fleet": "Fleet availability not verified",
}

_POOL: ProcessPoolExecutor | None = None
_POOL_LOCK = threading.Lock()


def pool() -> ProcessPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ProcessPoolExecutor(max_workers=6)
        return _POOL


def _noop(*a, **k) -> None:
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(m):
    if isinstance(m, dict):
        return {k: _json(v) for k, v in m.items()}
    if isinstance(m, (list, tuple)):
        return [_json(v) for v in m]
    if isinstance(m, np.ndarray):
        return m.tolist()
    if isinstance(m, (np.floating, np.integer, np.bool_)):
        return m.item()
    return m


@dataclass
class DataResult:
    revision: int
    matrix: str
    preset_id: str
    seed: int
    scale: float
    fused_raw: pd.DataFrame = field(repr=False)
    quality: dict = field(default_factory=dict)
    privacy: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    input_fp: str = ""
    provenance: dict = field(default_factory=dict)

    def public(self) -> dict:
        p = PRESET_BY_ID[self.preset_id]
        return {"revision": self.revision, "matrix": self.matrix, "preset_id": self.preset_id,
                "preset_name": p["name"], "preset_line": p["line"], "seed": self.seed,
                "scale": self.scale, "quality": self.quality, "privacy": self.privacy,
                "counts": self.counts, "input_fingerprint": self.input_fp,
                "provenance": self.provenance,
                "modelled_daily_boardings": float(self.fused_raw["demand"].sum()) * self.scale,
                "source": "synthetic",
                "source_label": ("Frozen thesis matrix (synthetic)" if self.matrix == "thesis"
                                 else "Generated synthetic data")}


class Engine:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.gateway, _ = build_gateway(SERVICE_CONFIG)
        self.cfg = runctx.load_config(SERVICE_CONFIG)
        self.ref = self.gateway.ref
        self.api_key = self.cfg["service"]["api_key"]
        self.protocol = json.loads(protocol_path().read_text(encoding="utf-8"))
        self.protocol_fp = rc.protocol_fingerprint(self.protocol)
        self._gen_ready = False
        self._quality_cache: dict = {}
        base = setup(self.cfg, pd.read_csv(THESIS_MATRIX))
        self.stop_ids, self.params = base.stop_ids, base.params

    # -- layer 0 ----------------------------------------------------------------
    def _load_generator(self) -> None:
        if self._gen_ready:
            return
        with self._lock:
            if self._gen_ready:
                return
            from config import load_config as load_gen_config
            from geo import build_corridor
            self.gen_cfg = load_gen_config(GEN_CONFIG)
            sa = self.gen_cfg["study_area"]
            self.corridor = build_corridor(
                stops_csv=f"{self.gen_cfg['run']['data_dir']}/{sa['stops_csv']}",
                res=sa["h3_resolution"], buffer_m=sa["catchment_buffer_m"],
                anchors=self.gen_cfg["attraction_anchors"], cordon_zones=sa["cordon_zones"],
                density_model=self.gen_cfg["population"]["home_density_model"])
            self.persons = pd.read_parquet(GEN_DIR / "output" / "persons.parquet")
            self.trips = pd.read_parquet(GEN_DIR / "output" / "trips.parquet")
            self.oracle = pd.read_csv(ORACLE)
            self._gen_ready = True

    def _effective_gen_cfg(self, preset_id: str, seed: int, extra: dict | None = None) -> dict:
        g = copy.deepcopy(self.gen_cfg)
        g["run"]["random_seed"] = int(seed)
        for feed, kv in PRESET_BY_ID[preset_id]["degradation"].items():
            g["degradation"][feed].update(kv)
        for feed, kv in (extra or {}).items():
            g["degradation"][feed].update(kv)
        return g

    def _fuse(self, gcfg: dict, seed: int, progress: Progress = _noop):
        from feed_atto import generate_atto
        from feed_telecom import generate_telecom
        rng = np.random.default_rng(int(seed))
        progress("Generating synthetic feeds", 1, 5, "fare taps")
        atto = generate_atto(gcfg, self.persons, self.trips, rng)
        progress("Generating synthetic feeds", 2, 5, "signalling events")
        tel = generate_telecom(gcfg, self.persons, self.trips, self.corridor, rng)
        progress("Checking feeds (k-anonymity)", 3, 5, "")
        ra = self.gateway.ingest("atto", atto, api_key=self.api_key)
        rt = self.gateway.ingest("telecom", tel, api_key=self.api_key)
        if not (ra.accepted and rt.accepted):
            raise RuntimeError("A feed failed the Layer-1 checks and was quarantined.")
        progress("Estimating demand", 4, 5, "fusion")
        tod = build_telecom_od(rt.index.indexed, sample_rate=0.138)
        ch = chain_alightings(ra.index.indexed)
        fused = fuse(ra.index.indexed, tod, ch, self.ref, walk_m=400)
        return fused, ra, rt, len(atto), len(tel)

    # -- step 1: data ---------------------------------------------------------------
    def run_data(self, revision: int, preset_id: str, seed: int, matrix: str, scale: float,
                 progress: Progress = _noop) -> DataResult:
        t0 = time.time()
        oracle = pd.read_csv(ORACLE)
        if matrix == "thesis":
            progress("Loading the frozen thesis matrix", 0, 2, "")
            fused = pd.read_csv(THESIS_MATRIX)
            m, _ = validate(fused, oracle, known_stops=self.stop_ids, full_precision=True)
            quality = {"alighting_distribution_similarity": m["alight_cosine_wtd"],
                       "top20_od_recall": m["top20_od_recall"],
                       "top20_od_matched": (None if m["top20_od_recall"] is None
                                            else int(round(m["top20_od_recall"] * 20))),
                       "total_error_pct": m["total_error_pct"],
                       "reason_codes": m["reason_codes"],
                       "validation_version": VALIDATION_VERSION,
                       "matrix_sha256": _sha(THESIS_MATRIX), "oracle_sha256": _sha(ORACLE),
                       "note": "Computed now for this exact matrix and oracle pair."}
            privacy = {"value": None, "reason_code": "not_recorded_for_frozen_matrix",
                       "message": "Suppression counts were not recorded when the frozen matrix "
                                  "was produced."}
            counts = {"fare_taps": None, "signalling_events": None}
            preset_id, seed = "as_is", 0
            prov = {"mode": "frozen", "path": "06_Results/tables/fused_demand_matrix.csv"}
        else:
            self._load_generator()
            gcfg = self._effective_gen_cfg(preset_id, seed)
            fused, ra, rt, n_atto, n_tel = self._fuse(gcfg, seed, progress)
            m, _ = validate(fused, self.oracle, known_stops=self.stop_ids, full_precision=True)
            quality = {"alighting_distribution_similarity": m["alight_cosine_wtd"],
                       "top20_od_recall": m["top20_od_recall"],
                       "top20_od_matched": (None if m["top20_od_recall"] is None
                                            else int(round(m["top20_od_recall"] * 20))),
                       "total_error_pct": m["total_error_pct"],
                       "reason_codes": m["reason_codes"], "validation_version": VALIDATION_VERSION,
                       "matrix_sha256": None, "oracle_sha256": _sha(ORACLE),
                       "note": "Computed now for the matrix generated in this run."}
            ai, ti = ra.index.stats, rt.index.stats
            recv_cells = ai["cells_total"] + ti["cells_total"]
            privacy = {"k": int(ai["k_anonymity"]),
                       "cells_received": int(recv_cells),
                       "cells_suppressed": int(ai["cells_suppressed"] + ti["cells_suppressed"]),
                       "cells_suppressed_share": ((ai["cells_suppressed"] + ti["cells_suppressed"]) /
                                                  recv_cells if recv_cells else None),
                       "suppress_downstream": bool(ai["suppress_downstream"]),
                       "note": ("Suppression applies to published aggregates; the optimiser "
                                "input is not suppressed in this profile."
                                if not ai["suppress_downstream"] else
                                "Suppressed cells are withheld from the demand estimate.")}
            counts = {"fare_taps": int(n_atto), "signalling_events": int(n_tel)}
            prov = {"mode": "computed", "preset": preset_id, "seed": int(seed)}
        peak = float(PRESET_BY_ID[preset_id]["demand"].get("peak_multiplier", 1.0))
        if peak != 1.0:
            hours = set(PERIODS["am_peak"]) | set(PERIODS["pm_peak"])
            fused = fused.copy()
            mask = fused["hour"].isin(hours)
            fused.loc[mask, "demand"] = fused.loc[mask, "demand"] * peak
        cfg = copy.deepcopy(self.cfg)
        cfg["operations"]["demand_scale"] = float(scale)
        prov.update({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - t0, 2)})
        progress("Demand estimate ready", 2 if matrix == "thesis" else 5,
                 2 if matrix == "thesis" else 5, "")
        return DataResult(revision=revision, matrix=matrix, preset_id=preset_id, seed=int(seed),
                          scale=float(scale), fused_raw=fused, quality=quality, privacy=privacy,
                          counts=counts, input_fp=provenance.input_fp(fused, cfg), provenance=prov)

    def _cfg_for(self, data: DataResult, tco: float | None = None, delta: float | None = None) -> dict:
        cfg = copy.deepcopy(self.cfg)
        cfg["operations"]["demand_scale"] = data.scale
        if tco is not None:
            cfg["operations"]["tco_factor"] = tco
        if delta is not None:
            cfg["operations"]["equity_delta_default"] = delta
        return cfg

    # -- step 2: plans --------------------------------------------------------------
    def _optimise(self, cfg: dict, fused: pd.DataFrame, scale: float, seeds, progress: Progress,
                  stage: str, done0: int = 0, total: int | None = None) -> list:
        tasks = [(cfg, fused, scale, None, s) for s in seeds]
        total = total or len(tasks)
        cands, done = [], done0
        progress(stage, done, total, f"{done} of {total} optimiser runs")
        futs = [pool().submit(optimise_one_seed, t) for t in tasks]
        for f in as_completed(futs):
            cands.extend(f.result())
            done += 1
            progress(stage, done, total, f"{done} of {total} optimiser runs")
        return cands

    def front(self, data: DataResult, budget_idx: int, delta_idx: int, revision: dict,
              progress: Progress = _noop) -> dict:
        tco = BUDGET_STOPS[budget_idx]["tco_factor"]
        delta = DELTA_STOPS[delta_idx]["delta"]
        cfg = self._cfg_for(data, tco, delta)
        seeds = tuple(self.protocol["optimisation"]["seeds"])
        t0 = time.time()
        cands = self._optimise(cfg, data.fused_raw, data.scale, seeds, progress, "Calculating plans")
        st = setup(cfg, data.fused_raw)
        if not cands:
            raise RuntimeError("The optimiser found no plan within these limits.")
        sel = select_from_candidates(st, cands)
        alts: dict[str, dict] = {}

        def add(key: str, cand, trips, disp):
            if cand is None:
                return
            for a in alts.values():
                if a["cid"] == cand.cid:
                    a["keys"].append(key)
                    return
            alts[key] = {"keys": [key], "cid": cand.cid, "candidate": cand, "trips": trips,
                         "dispatch": disp}

        add("recommended", sel["candidate"], sel["trips"], sel["dispatch"])
        for key, sort_key in (("least_waiting", lambda c: (c.wait, c.cost, c.cid)),
                              ("least_crowding", lambda c: (c.crowd_min, c.cost, c.cid))):
            ranked = sorted(cands, key=sort_key)
            c, trips, disp, _ = select_executable(ranked[:60], st.summary, st.params, st.budget,
                                                  st.delta, label="candidate")
            add(key, c, trips, disp)
        alternatives = []
        for a in alts.values():
            fz = feasibility(a["dispatch"], st.budget, st.delta, st.params["fleet"])
            alternatives.append({
                "id": a["cid"], "keys": a["keys"],
                "label": " / ".join(ALT_TITLES[k] for k in a["keys"]),
                "headways": a["candidate"].headways.tolist(), "shares": a["candidate"].shares.tolist(),
                "dispatch_metrics": _json({k: v for k, v in a["dispatch"].items()}),
                "feasibility": fz, "plan_fingerprint": provenance.plan_fp(a["trips"]),
                "_trips": a["trips"]})
        base_fz = feasibility(st.base_disp, st.budget, st.delta, st.params["fleet"])
        ranked_all = sel["ranked"]
        front = {
            "revision": revision,
            "conditions": {"budget_idx": budget_idx, "delta_idx": delta_idx, "tco_factor": tco,
                           "delta": delta, "scale": data.scale,
                           "budget_label": BUDGET_STOPS[budget_idx]["label"],
                           "delta_label": DELTA_STOPS[delta_idx]["label"]},
            "budget": st.budget,
            "baseline": {"headways": st.bh.tolist(), "shares": st.bf.tolist(),
                         "dispatch_metrics": _json(st.base_disp), "feasibility": base_fz,
                         "plan_fingerprint": provenance.plan_fp(st.base_trips),
                         "_trips": st.base_trips},
            "alternatives": alternatives,
            "all_plans": [{"id": c.cid, "rank": i, "wait": c.wait, "crowd_min": c.crowd_min,
                           "gen_cost": c.gen_cost, "cost": c.cost,
                           "max_headway": float(max(c.headways))}
                          for i, c in enumerate(ranked_all)],
            "selection_log": sel["log"],
            "selection_rule": self.protocol["optimisation"]["selection_rule"],
            "algorithm": {"population": st.pop, "generations": st.gen, "seeds": list(seeds)},
            "fingerprints": {"input": data.input_fp, "model": provenance.model_fp(),
                             "protocol": self.protocol_fp},
            "crowding": st.params["crowding"],
            "seconds": round(time.time() - t0, 1),
            "evidence_kind": "model_only",
        }
        for alt in front["alternatives"]:
            alt["registered_result_available"] = self.registered(data, front, alt)
        return front

    def plan_table(self, front: dict, alt_id: str) -> dict:
        alt = next(a for a in front["alternatives"] if a["id"] == alt_id)
        base = front["baseline"]
        rows = []
        act = {(r["period"], r["direction"]): r for r in alt["dispatch_metrics"]["by_period_direction"]}
        for k, per in enumerate(PERIOD_NAMES):
            a = act[(per, "fwd")]
            rows.append({"period": per, "label": PERIOD_LABELS[per],
                         "baseline_headway_min": base["headways"][k],
                         "plan_headway_min": alt["headways"][k],
                         "plan_effective_headway_min": alt["dispatch_metrics"]["effective_headways"][per],
                         "trips_per_direction": a["trips"], "trips_18m_per_direction": a["trips_18m"],
                         "share_18m_desired": alt["shares"][k], "share_18m_actual": a["share_18m_actual"]})
        return {"id": alt_id, "label": alt["label"], "rows": rows}

    # -- step 3: simulation ------------------------------------------------------------
    def registered(self, data: DataResult, front: dict, alt: dict) -> bool:
        rel = archive.active_release()
        if not rel or not rel.get("layer3_result.json") or not rel.get("simulation_analysis.json"):
            return False
        fp = rel["layer3_result.json"]["fingerprints"]
        expected = {"input": data.input_fp, "plan_candidate": alt["plan_fingerprint"],
                    "plan_baseline": front["baseline"]["plan_fingerprint"],
                    "model": provenance.model_fp(), "protocol": self.protocol_fp}
        if any(fp.get(k) != expected[k] for k in ("input", "plan_candidate", "plan_baseline", "model")):
            return False
        release_protocol = json.loads((Path(rel["path"]) / "protocol.json").read_text(encoding="utf-8"))
        if self.protocol_fp != rc.protocol_fingerprint(release_protocol):
            return False
        counts = {"baseline": len(self.protocol["experiment"]["confirmatory_series"]["baseline_seeds"]),
                  "candidate": len(self.protocol["experiment"]["confirmatory_series"]["candidate_seeds"])}
        return rc.validate_simulation_analysis(rel["simulation_analysis.json"],
                                               expected_fps=expected,
                                               expected_counts=counts)["applicable"]

    def simulate(self, data: DataResult, front: dict, alt_id: str, workdir: Path,
                 progress: Progress = _noop) -> dict:
        import run_replications as rr
        alt = next(a for a in front["alternatives"] if a["id"] == alt_id)
        registered = self.registered(data, front, alt)
        rel = archive.active_release() if registered else None
        if registered and rel.get("simulation_analysis.json"):
            sim = copy.deepcopy(rel["simulation_analysis.json"])
            sim["source"] = f"registered release {rel['release_id']} (not re-run)"
            progress("Reading the registered simulation", 1, 1, "")
            return self._sim_status(sim, registered=True)
        sp = self.cfg["simulation"]
        prot = self.protocol
        seeds = {"baseline": prot["experiment"]["confirmatory_series"]["baseline_seeds"],
                 "candidate": prot["experiment"]["confirmatory_series"]["candidate_seeds"]}
        fused = data.fused_raw.copy()
        fused["demand"] = fused["demand"] * data.scale
        activity, od = stop_activity(fused, self.stop_ids), am_od(fused, self.stop_ids)
        net_dir = workdir / "network"
        net, edges, bstops = build_network_bidir(self.stop_ids, self.ref.stops, sp["link_speed_kmh"],
                                                 net_dir, lanes=int(sp["replications"]["traffic_lanes"]))
        trips = {"baseline": front["baseline"]["_trips"], "candidate": alt["_trips"]}
        speed = float(prot["simulation"]["boarding_speedup_18m"]["central"])
        jobs = [{"condition": c, "seed": int(s), "tag": f"{c}_{s}", "speedup": speed,
                 "run_dir": str(workdir / "sim" / f"{c}_{s}"), "net": net,
                 "add": net_dir / "stops.add.xml", "edges": edges, "bstops": bstops,
                 "trips": trips[c], "activity": activity, "od_am": od,
                 "sim": {"dwell_base_s": sp["dwell_base_s"], "dwell_per_pax_s": sp["dwell_per_pax_s"]},
                 "teleport_s": int(prot["simulation"]["time_to_teleport_s"]),
                 "traffic_period_s": int(sp["replications"]["traffic_period_s"])}
                for c, sl in seeds.items() for s in sl]
        t0 = time.time()
        results, done = [], 0
        futs = [pool().submit(rr._one_run, j) for j in jobs]
        for f in as_completed(futs):
            r = f.result()
            r.pop("_buses", None)
            results.append(r)
            done += 1
            progress("Simulating", done, len(jobs), f"{done} of {len(jobs)} runs completed")
        analysis = analyse(results, series="protocol_rerun", protocol_passed=True,
                           evidence_kind="exploratory")
        analysis.update({
            "schema_version": rc.SCHEMA_VERSION, "wall_seconds": time.time() - t0,
            "fingerprints": {"input": data.input_fp, "plan_baseline": front["baseline"]["plan_fingerprint"],
                             "plan_candidate": alt["plan_fingerprint"], "model": provenance.model_fp(),
                             "protocol": self.protocol_fp},
            "candidate_id": alt_id, "boarding_speedup_18m": speed,
            "runs": [{k: v for k, v in r.items() if k in ("condition", "seed", "valid", "primary_min",
                                                          "problems", "seconds")} for r in results],
            "source": "simulated in this session with the protocol seeds",
        })
        return self._sim_status(analysis, registered=False)

    @staticmethod
    def _sim_status(sim: dict, registered: bool) -> dict:
        complete = sim.get("completion_status") == "complete"
        if not complete:
            key = "incomplete"
        else:
            key = "evaluated"
        sim["status_key"] = key
        sim["status_label"] = STATUS_LABELS[key]
        sim["registered"] = registered
        sim["evidence_kind"] = "confirmatory" if registered else "exploratory"
        if not registered and complete:
            sim["evidence_note"] = ("This scenario is not a registered confirmatory series; the "
                                    "result is exploratory and does not confirm H1_bus_v2.")
        return sim

    # -- step 3: economics ----------------------------------------------------------
    def economics(self, data: DataResult, front: dict, alt_id: str, simulation: dict | None) -> dict:
        alt = next(a for a in front["alternatives"] if a["id"] == alt_id)
        base, cand = front["baseline"], alt
        op = self.cfg["operations"]
        res = {c: {"all": resources(t, self.params, one_way_min=op["one_way_min"], layover_min=op["layover_min"]),
                   "am": resources(t, self.params, one_way_min=op["one_way_min"], layover_min=op["layover_min"],
                                   periods=["am_peak"])}
               for c, t in (("baseline", base["_trips"]), ("candidate", cand["_trips"]))}
        fps = {"input": data.input_fp, "plan_baseline": base["plan_fingerprint"],
               "plan_candidate": cand["plan_fingerprint"], "model": provenance.model_fp(),
               "protocol": self.protocol_fp,
               "counts": {"baseline": len(self.protocol["experiment"]["confirmatory_series"]["baseline_seeds"]),
                          "candidate": len(self.protocol["experiment"]["confirmatory_series"]["candidate_seeds"])}}
        sim_for_eco = None
        if simulation is not None and simulation.get("registered"):
            sim_for_eco = dict(simulation, series="confirmatory", evidence_kind="confirmatory")
        ivt, ivt_reason, verdict = eco.simulation_ivt(sim_for_eco, fps)
        if simulation is not None and not simulation.get("registered"):
            ivt_reason = "simulation_not_registered"
        rate = op.get("cost_per_veh_km_uzs_12m")
        bm, cm = base["dispatch_metrics"], cand["dispatch_metrics"]
        am = eco.scope_block(scope="am_peak",
                             wait_base=bm["by_period"]["am_peak"]["wait"], wait_plan=cm["by_period"]["am_peak"]["wait"],
                             crowd_base=bm["by_period"]["am_peak"]["crowd_min"],
                             crowd_plan=cm["by_period"]["am_peak"]["crowd_min"],
                             K_base=res["baseline"]["am"]["equiv_12m_km"], K_plan=res["candidate"]["am"]["equiv_12m_km"],
                             ivt_benefit_pax_min=ivt, ivt_reason=ivt_reason, rate_12m=rate, fingerprints=fps)
        day = eco.scope_block(scope="all_day_model", wait_base=bm["wait"], wait_plan=cm["wait"],
                              crowd_base=bm["crowd_min"], crowd_plan=cm["crowd_min"],
                              K_base=res["baseline"]["all"]["equiv_12m_km"],
                              K_plan=res["candidate"]["all"]["equiv_12m_km"],
                              ivt_benefit_pax_min=None, ivt_reason="not_carried_over_from_morning_simulation",
                              rate_12m=rate, fingerprints=fps)
        return {"economics_version": eco.ECONOMICS_VERSION, "value_of_time": eco.VOT_BASIS,
                "days_per_year": {"value": eco.DAYS_PER_YEAR, "evidence_type": "scenario_assumption"},
                "operator_rate": {"value": rate, "status": "known" if rate is not None else "unknown"},
                "resources": res, "simulation_verdict": verdict,
                "scopes": {"am_peak": am, "all_day_model": day,
                           "all_day_with_simulation": eco.not_evaluated(
                               "all_day_with_simulation", "not_evaluated",
                               "Only the morning cohort is simulated.")}}

    # -- step 3: re-optimise for each scenario ---------------------------------------
    def holds(self, data: DataResult, budget_idx: int, delta_idx: int, progress: Progress = _noop) -> dict:
        seeds = tuple(self.protocol["optimisation"]["seeds"])
        conds = ([("budget", b["idx"], b["label"], b["tco_factor"], DELTA_STOPS[delta_idx]["delta"])
                  for b in BUDGET_STOPS] +
                 [("delta", d["idx"], d["label"], BUDGET_STOPS[budget_idx]["tco_factor"], d["delta"])
                  for d in DELTA_STOPS])
        total = len(conds) * len(seeds)
        progress("Re-optimising for each scenario", 0, total, f"0 of {total} optimiser runs")
        futs = {}
        for c in conds:
            cfg = self._cfg_for(data, c[3], c[4])
            for s in seeds:
                futs[pool().submit(optimise_one_seed, (cfg, data.fused_raw, data.scale, None, s))] = c
        pools: dict = {}
        done = 0
        for f in as_completed(futs):
            pools.setdefault(futs[f], []).extend(f.result())
            done += 1
            progress("Re-optimising for each scenario", done, total, f"{done} of {total} optimiser runs")
        out = {"budget": [], "delta": []}
        for c in conds:
            st = setup(self._cfg_for(data, c[3], c[4]), data.fused_raw)
            sel = select_from_candidates(st, pools.get(c, []))
            row = {"idx": c[1], "label": c[2], "tco_factor": c[3], "delta": c[4]}
            if sel["candidate"] is None:
                row.update({"feasible": False, "reason": "no_feasible_executable_candidate"})
            else:
                m, b = sel["dispatch"], st.base_disp
                row.update({"feasible": True, "id": sel["candidate"].cid, "gen_cost": m["gen_cost"],
                            "gen_cost_change_pct": 100 * (m["gen_cost"] - b["gen_cost"]) / b["gen_cost"],
                            "wait_change_pct": 100 * (m["wait"] - b["wait"]) / b["wait"],
                            "cost_change_pct": 100 * (m["cost"] - b["cost"]) / b["cost"],
                            "worst_headway_min": max(m["effective_headways"].values())})
            out[c[0]].append(row)
        out["quality"] = self.quality_response(data, progress)
        out["mode"] = "Re-optimise for each scenario"
        out["note"] = ("Each point re-runs the optimiser and may choose a different plan; this is "
                       "not a robustness test of one fixed plan.")
        return out

    def quality_response(self, data: DataResult, progress: Progress = _noop) -> dict:
        """Alighting similarity vs fare-tap loss, re-run from the *effective* scenario
        configuration (the preset's degradations kept)."""
        if data.matrix == "thesis":
            return {"value": None, "reason_code": "frozen_matrix_generator_config_unknown",
                    "message": "The generator settings of the frozen matrix are not recorded, so "
                               "a tap-loss series cannot be built for it."}
        self._load_generator()
        rows = []
        for stop in QUALITY_STOPS:
            g = self._effective_gen_cfg(data.preset_id, data.seed,
                                        {"atto": {"p_untapped": stop["p_untapped"]}})
            key = hashlib.sha256(json.dumps([g, data.seed, VALIDATION_VERSION], sort_keys=True,
                                            default=str).encode()).hexdigest()
            if key not in self._quality_cache:
                progress("Checking data quality", stop["idx"], len(QUALITY_STOPS),
                         f"running tap loss {stop['label']}")
                fused, ra, rt, n_atto, _ = self._fuse(g, data.seed)
                m, _ = validate(fused, self.oracle, known_stops=self.stop_ids, full_precision=True)
                self._quality_cache[key] = {"similarity": m["alight_cosine_wtd"],
                                            "top20_od_recall": m["top20_od_recall"],
                                            "fare_taps": int(n_atto)}
            rows.append({"idx": stop["idx"], "label": stop["label"], **self._quality_cache[key]})
        return {"rows": rows, "preset": data.preset_id, "seed": data.seed,
                "note": "Data quality changes the demand estimate; the plan is not re-optimised here."}


_engine: Engine | None = None
_engine_lock = threading.Lock()


def engine() -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = Engine()
        return _engine
