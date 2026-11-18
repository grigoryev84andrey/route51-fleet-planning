"""Historical results, read only through the historical adapter.

The tables in `06_Results/tables` were produced before schema 2 (crowding weight
10, single-direction simulation, fixed vehicle type per window). They are shown
only as a labelled historical reference; they never confirm a plan, never feed
the economics and never replace a missing simulation.
"""
from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path

import pandas as pd

from settings import ACTIVE_RELEASE, RESULTS_DIR, TABLES_DIR, install_import_paths

install_import_paths()
import result_contracts as rc  # noqa: E402


class ArchiveMissing(RuntimeError):
    pass


@functools.lru_cache(maxsize=32)
def table(name: str) -> pd.DataFrame:
    p = Path(TABLES_DIR) / name
    if not p.exists():
        raise ArchiveMissing(f"{name} not found in {TABLES_DIR}")
    return pd.read_csv(p)


def historical_simulation() -> dict | None:
    """The pre-schema-2 simulation summary, wrapped as a historical reference."""
    try:
        s34 = table("sec34_stats.csv").iloc[0]
    except ArchiveMissing:
        return None
    old = {"source": "06_Results/tables/sec34_stats.csv",
           "n_per_condition": int(s34["n_per_condition"]),
           "mean_diff_min": float(s34["mean_diff"]),
           "metric": "single-direction peak-hour mean bus travel time (historical definition)"}
    return rc.from_legacy_simulation(old)


def active_release() -> dict | None:
    """The accepted release (after acceptance only), with its result objects."""
    if not ACTIVE_RELEASE.exists():
        return None
    ptr = json.loads(ACTIVE_RELEASE.read_text(encoding="utf-8"))
    base = RESULTS_DIR / ptr["path"]
    manifest_path = base / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("release_id") != ptr.get("release_id"):
        return None
    checks = {entry["path"]: entry["sha256"] for entry in manifest.get("files", [])}
    checks[manifest["protocol"]["path"]] = manifest["protocol"]["sha256"]
    for name in ("tables/layer3_result.json", "tables/simulation_analysis.json", "protocol.json"):
        path = base / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != checks.get(name):
            return None
    out = {"release_id": ptr["release_id"], "path": str(base)}
    for name in ("layer3_result.json", "simulation_analysis.json", "economics_result.json",
                 "manifest.json"):
        # the release keeps its result objects in `tables/` and the manifest at the root
        p = next((c for c in (base / name, base / "tables" / name) if c.exists()), None)
        out[name] = json.loads(p.read_text(encoding="utf-8")) if p else None
    return out
