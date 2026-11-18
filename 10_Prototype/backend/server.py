"""Fleet Planning Console — HTTP service.

    python backend/server.py            # then open http://127.0.0.1:8051

Three steps: Conditions → Compare plans → Validate & export. Long work runs as
jobs (see jobs.py). Every job carries the revision of the conditions it was
started for; its result becomes current only if those conditions are still the
current ones. The session lives in this process's memory: after a server
restart the console reports that the state was lost instead of polling for
jobs that no longer exist.
"""
from __future__ import annotations

import math
import sys
import threading
import webbrowser
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import uvicorn                                                  # noqa: E402
from fastapi import Body, FastAPI, HTTPException                # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles                     # noqa: E402

import archive                                                  # noqa: E402
import pipeline                                                 # noqa: E402
import report                                                   # noqa: E402
from jobs import SERVER_INSTANCE, registry                      # noqa: E402
from settings import (BUDGET_STOPS, DEFAULT_BUDGET_IDX, DEFAULT_DELTA_IDX,  # noqa: E402
                      DEFAULT_SCALE, DELTA_STOPS, DEMAND_SCALES, EXPORTS_DIR, FRONTEND_DIR,
                      MATRICES, PRESET_BY_ID, PRESETS, QUALITY_STOPS, SOURCES, capabilities)

app = FastAPI(title="Route 51 — Fleet Planning Console", version="2.0")

_LOCK = threading.RLock()
SESSION: dict = {
    "rev": {"data": 0, "front": 0},
    "data": None, "front": None, "selected": None,
    "sims": {},            # (front_revision, plan id) -> simulation result
    "holds": None,         # {"front_revision": r, "result": ...}
    "jobs": {},            # kind -> latest job id
    "scenarios": [],
}


# --- request validation -------------------------------------------------------
def _bad(code: str, message: str):
    raise HTTPException(422, {"code": code, "message": message})


def _int(body: dict, key: str, lo: int, hi: int, default=None) -> int:
    v = body.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or int(v) != v:
        _bad("invalid_integer", f"{key} must be a whole number")
    v = int(v)
    if not lo <= v <= hi:
        _bad("out_of_range", f"{key} must lie in [{lo}, {hi}]")
    return v


def _choice(body: dict, key: str, allowed, default=None):
    v = body.get(key, default)
    if v not in allowed:
        _bad("unsupported_value" if key != "source" else "unsupported_source",
             f"{key} must be one of {list(allowed)}")
    return v


def _front_public(f: dict | None) -> dict | None:
    if f is None:
        return None
    out = {k: v for k, v in f.items() if k not in ("baseline", "alternatives")}
    out["baseline"] = {k: v for k, v in f["baseline"].items() if not k.startswith("_")}
    out["alternatives"] = [{k: v for k, v in a.items() if not k.startswith("_")}
                           for a in f["alternatives"]]
    return out


def _current_sim():
    f, sel = SESSION["front"], SESSION["selected"]
    if f is None or sel is None:
        return None
    return SESSION["sims"].get((f["revision"]["front"], sel))


def _stale() -> dict:
    d, f = SESSION["data"], SESSION["front"]
    return {"data": d is not None and d.revision != SESSION["rev"]["data"],
            "front": (f is not None and (f["revision"]["front"] != SESSION["rev"]["front"] or
                                         d is None or f["revision"]["data"] != d.revision))}


def _job_ref(kind: str, job) -> dict:
    SESSION["jobs"][kind] = job.id
    return {"job": job.public()}


# --- reference -------------------------------------------------------------
@app.get("/api/config")
def config():
    e = pipeline.engine()
    op = e.cfg["operations"]
    return {
        "server_instance": SERVER_INSTANCE,
        "corridor": {"name": "Route 51", "stops": len(e.stop_ids), "length_km": op["route_km"],
                     "one_way_min": op["one_way_min"], "fleet": op["fleet"],
                     "headway_max_min": op["headway_max_min"],
                     "headway_min_min": e.params["h_min"],
                     "note": "Route 51 is the only corridor in this version."},
        "presets": [{"id": p["id"], "name": p["name"], "line": p["line"]} for p in PRESETS],
        "scales": DEMAND_SCALES, "default_scale": DEFAULT_SCALE,
        "budget_stops": BUDGET_STOPS, "delta_stops": DELTA_STOPS, "quality_stops": QUALITY_STOPS,
        "defaults": {"budget_idx": DEFAULT_BUDGET_IDX, "delta_idx": DEFAULT_DELTA_IDX},
        "protocol": {"id": e.protocol["protocol_id"], "version": e.protocol["protocol_version"],
                     "hypothesis": e.protocol["hypothesis"]["statement"],
                     "crowding": e.protocol["crowding"]["formula"],
                     "seeds": e.protocol["optimisation"]["seeds"],
                     "simulation_runs": len(e.protocol["experiment"]["confirmatory_series"]["baseline_seeds"])},
        "capabilities": capabilities(),
        "historical": archive.historical_simulation(),
        "release": (lambda r: {"release_id": r["release_id"]} if r else None)(archive.active_release()),
        "import_note": "Operational data import is not available in this version",
    }


# --- step 1 ----------------------------------------------------------------
@app.post("/api/data/run")
def data_run(body: dict = Body(...)):
    _choice(body, "source", SOURCES, "synthetic")
    matrix = _choice(body, "matrix", MATRICES, "thesis")
    preset = _choice(body, "preset", list(PRESET_BY_ID), "as_is")
    seed = _int(body, "seed", 0, 2**31 - 1, 42)
    scale = body.get("scale", DEFAULT_SCALE)
    if scale not in DEMAND_SCALES:
        _bad("unsupported_value", f"scale must be one of {DEMAND_SCALES}")
    with _LOCK:
        SESSION["rev"]["data"] += 1
        rev = SESSION["rev"]["data"]

    def work(progress, job):
        return pipeline.engine().run_data(rev, preset, seed, matrix, float(scale), progress)

    def publish(job) -> bool:
        with _LOCK:
            if SESSION["rev"]["data"] != rev:
                return False
            SESSION.update(data=job.result, front=None, selected=None, holds=None)
            return True

    return _job_ref("data", registry.submit("data", {"data": rev}, work, publish))


# --- step 2 ----------------------------------------------------------------
@app.post("/api/plan/front")
def plan_front(body: dict = Body(...)):
    data = SESSION["data"]
    if data is None:
        _bad("no_data", "Prepare the scenario first.")
    if _stale()["data"]:
        _bad("data_stale", "The data conditions changed; wait for the new demand estimate.")
    bi = _int(body, "budget_idx", 0, len(BUDGET_STOPS) - 1, DEFAULT_BUDGET_IDX)
    di = _int(body, "delta_idx", 0, len(DELTA_STOPS) - 1, DEFAULT_DELTA_IDX)
    with _LOCK:
        SESSION["rev"]["front"] += 1
        rev = {"data": data.revision, "front": SESSION["rev"]["front"]}

    def work(progress, job):
        return pipeline.engine().front(data, bi, di, rev, progress)

    def publish(job) -> bool:
        with _LOCK:
            d = SESSION["data"]
            if SESSION["rev"]["front"] != rev["front"] or d is None or d.revision != rev["data"]:
                return False
            f = job.result
            SESSION.update(front=f, selected=f["alternatives"][0]["id"] if f["alternatives"] else None,
                           holds=None)
            return True

    return _job_ref("front", registry.submit("front", rev, work, publish))


@app.post("/api/plan/select")
def plan_select(body: dict = Body(...)):
    f = SESSION["front"]
    if f is None:
        _bad("no_plans", "Calculate plans first.")
    pid = body.get("id")
    if pid not in {a["id"] for a in f["alternatives"]}:
        _bad("unknown_plan", "This plan is not among the compared alternatives.")
    SESSION["selected"] = pid
    return {"selected": pid, "table": pipeline.engine().plan_table(f, pid),
            "simulation": _current_sim()}


# --- step 3 ----------------------------------------------------------------
@app.post("/api/result/simulate")
def result_simulate(body: dict = Body(default={})):
    data, f, sel = SESSION["data"], SESSION["front"], SESSION["selected"]
    if f is None or sel is None:
        _bad("no_plan", "Select a plan first.")
    if any(_stale().values()):
        _bad("stale", "Conditions changed; recalculate plans first.")
    alt = next(a for a in f["alternatives"] if a["id"] == sel)
    if not pipeline.engine().registered(data, f, alt) and not capabilities()["simulation"]:
        _bad("simulation_unavailable", "SUMO is not available on this machine.")
    rev = {"data": data.revision, "front": f["revision"]["front"], "plan": sel}

    def work(progress, job):
        return pipeline.engine().simulate(data, f, sel, job.workdir, progress)

    def publish(job) -> bool:
        with _LOCK:
            cur = SESSION["front"]
            if cur is None or cur["revision"]["front"] != rev["front"]:
                return False
            SESSION["sims"][(rev["front"], sel)] = job.result
            return True

    return _job_ref("simulate", registry.submit("simulate", rev, work, publish))


@app.post("/api/result/economics")
def result_economics(body: dict = Body(default={})):
    data, f, sel = SESSION["data"], SESSION["front"], SESSION["selected"]
    if f is None or sel is None:
        _bad("no_plan", "Select a plan first.")
    return pipeline.engine().economics(data, f, sel, _current_sim())


@app.post("/api/result/holds")
def result_holds(body: dict = Body(default={})):
    data, f = SESSION["data"], SESSION["front"]
    if f is None:
        _bad("no_plans", "Calculate plans first.")
    c = f["conditions"]
    rev = {"data": data.revision, "front": f["revision"]["front"]}

    def work(progress, job):
        return pipeline.engine().holds(data, c["budget_idx"], c["delta_idx"], progress)

    def publish(job) -> bool:
        with _LOCK:
            cur = SESSION["front"]
            if cur is None or cur["revision"]["front"] != rev["front"]:
                return False
            SESSION["holds"] = {"front_revision": rev["front"], "result": job.result}
            return True

    return _job_ref("holds", registry.submit("holds", rev, work, publish))


# --- scenarios (kept in this server session only) ----------------------------
@app.post("/api/scenario/save")
def scenario_save(body: dict = Body(default={})):
    f, sel = SESSION["front"], SESSION["selected"]
    if f is None or sel is None:
        _bad("no_plan", "Select a plan first.")
    alt = next(a for a in f["alternatives"] if a["id"] == sel)
    sim = _current_sim()
    entry = {"n": len(SESSION["scenarios"]) + 1, "plan": sel, "label": alt["label"],
             "conditions": f["conditions"], "gen_cost": alt["dispatch_metrics"]["gen_cost"],
             "status": sim["status_label"] if sim else pipeline.STATUS_LABELS["not_simulated"],
             "storage": "Available in this session"}
    SESSION["scenarios"].append(entry)
    return {"scenarios": SESSION["scenarios"]}


# --- export ----------------------------------------------------------------
@app.post("/api/export")
def export(body: dict = Body(default={})):
    wait_weight = _int(body, "wait_weight", 1, 2, 1)
    data, f, sel = SESSION["data"], SESSION["front"], SESSION["selected"]
    if f is None or sel is None:
        _bad("no_plan", "Select a plan first.")
    if any(_stale().values()):
        _bad("stale", "Conditions changed since the plans were calculated; recalculate first.")
    e = pipeline.engine()
    alt = next(a for a in f["alternatives"] if a["id"] == sel)
    sim = _current_sim()
    import time as _t
    bundle = {
        "schema_version": 2,
        "generated_at": _t.strftime("%Y-%m-%dT%H:%M:%S"),
        "data": data.public(),
        "front": {k: v for k, v in _front_public(f).items() if k not in ("all_plans",)},
        "baseline": {k: v for k, v in f["baseline"].items() if not k.startswith("_")},
        "plan": {k: v for k, v in alt.items() if not k.startswith("_")},
        "plan_table": e.plan_table(f, sel),
        "simulation": sim,
        "economics": e.economics(data, f, sel, sim),
        "holds": (SESSION["holds"] or {}).get("result"),
        "fingerprints": f["fingerprints"],
        "operational_feasibility": "unknown",
        "wait_weight": wait_weight,
        "protocol": {"id": e.protocol["protocol_id"], "version": e.protocol["protocol_version"]},
    }
    return report.write_export(bundle, f["baseline"]["_trips"], alt["_trips"])


@app.get("/api/export/file/{stem}/{name}")
def export_file(stem: str, name: str):
    root = Path(EXPORTS_DIR).resolve()
    path = (root / stem / name).resolve()
    if root not in path.parents or not path.exists():
        raise HTTPException(404, "not found")
    media = {"html": "text/html", "md": "text/markdown", "json": "application/json",
             "csv": "text/csv"}.get(name.rsplit(".", 1)[-1], "application/octet-stream")
    return FileResponse(path, media_type=media)


@app.get("/api/export/zip/{name}")
def export_zip(name: str):
    root = Path(EXPORTS_DIR).resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.exists():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="application/zip", filename=name)


# --- jobs and session ------------------------------------------------------
@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    j = registry.get(job_id)
    if j is None:
        return JSONResponse(status_code=404, content={
            "code": "job_unknown", "server_instance": SERVER_INSTANCE,
            "message": "This job is not known to the server (it may have been restarted)."})
    return j


@app.get("/api/session")
def session_state():
    d, f = SESSION["data"], SESSION["front"]
    e = pipeline.engine() if f is not None else None
    holds = SESSION["holds"]
    return {
        "server_instance": SERVER_INSTANCE,
        "revisions": SESSION["rev"],
        "stale": _stale(),
        "data": d.public() if d is not None else None,
        "front": _front_public(f),
        "selected": SESSION["selected"],
        "table": e.plan_table(f, SESSION["selected"]) if f is not None and SESSION["selected"] else None,
        "simulation": _current_sim(),
        "holds": holds["result"] if holds and f and holds["front_revision"] == f["revision"]["front"] else None,
        "jobs": {k: registry.get(v) for k, v in SESSION["jobs"].items()},
        "scenarios": SESSION["scenarios"],
    }


@app.get("/")
def index():
    return RedirectResponse("/ui/index.html")


class NoCacheStatic(StaticFiles):
    def is_not_modified(self, response_headers, request_headers) -> bool:  # noqa: D102
        return False

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


app.mount("/ui", NoCacheStatic(directory=str(FRONTEND_DIR), html=True), name="ui")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Fleet Planning Console")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8051)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    caps = capabilities()
    print("=" * 72)
    print("  Route 51 — Fleet Planning Console")
    print("=" * 72)
    for k in ("generator", "optimiser", "simulation"):
        print(f"  {k:<12}: {'available' if caps[k] else 'NOT AVAILABLE'}")
    for note in caps["notes"]:
        print(f"  note        : {note}")
    print(f"\n  Open http://{args.host}:{args.port}/\n")
    if not args.no_browser:
        try:
            webbrowser.open(f"http://{args.host}:{args.port}/")
        except Exception:                                        # pragma: no cover
            pass
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
