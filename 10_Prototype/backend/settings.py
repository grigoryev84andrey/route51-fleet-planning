"""Paths, presets and capability detection for the Fleet Planning Console prototype.

The prototype does not own any science. It locates the thesis pipeline
(`Final/05_Pipeline`), imports the shared computational modules and drives them.
Nothing here recomputes a scientific quantity.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

# --- locations -------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent
PROTOTYPE_DIR = BACKEND_DIR.parent
FINAL_DIR = PROTOTYPE_DIR.parent

PIPELINE_DIR = FINAL_DIR / "05_Pipeline"
SERVICE_DIR = PIPELINE_DIR / "route51_service"
GEN_DIR = PIPELINE_DIR / "route51_gen"
RESULTS_DIR = FINAL_DIR / "06_Results"
TABLES_DIR = RESULTS_DIR / "tables"
RELEASES_DIR = RESULTS_DIR / "releases"
ACTIVE_RELEASE = RESULTS_DIR / "active_release.json"
DEFAULT_PROTOCOL = RELEASES_DIR / "route51_v2_20260922" / "protocol.json"

FRONTEND_DIR = PROTOTYPE_DIR / "frontend"
EXPORTS_DIR = Path(os.environ.get("R51_EXPORTS_DIR") or (PROTOTYPE_DIR / "exports"))
WORKDIR = PROTOTYPE_DIR / ".work"          # job scratch; never a published artefact

SERVICE_CONFIG = SERVICE_DIR / "service_config.yaml"
GEN_CONFIG = GEN_DIR / "config.yaml"
THESIS_MATRIX = TABLES_DIR / "fused_demand_matrix.csv"
ORACLE = GEN_DIR / "output" / "ground_truth_bus_od.csv"

for d in (EXPORTS_DIR, WORKDIR):
    d.mkdir(parents=True, exist_ok=True)


def install_import_paths() -> None:
    """Put the shared pipeline packages on sys.path, service first."""
    for p in (SERVICE_DIR, GEN_DIR / "src"):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def protocol_path() -> Path:
    """Protocol of the active release if one is accepted, else the frozen draft protocol."""
    if ACTIVE_RELEASE.exists():
        rel = json.loads(ACTIVE_RELEASE.read_text(encoding="utf-8"))
        p = RESULTS_DIR / rel["path"] / "protocol.json"
        if p.exists():
            return p
    return DEFAULT_PROTOCOL


# --- Step-1 conditions -------------------------------------------------------
# Data sources: only generated synthetic data are implemented. Operational data
# import is not available in this version.
SOURCES = ("synthetic",)
MATRICES = ("thesis", "generate")

# Each preset is a real generator degradation setting, not a label.
PRESETS = [
    {"id": "as_is", "name": "As generated", "degradation": {}, "demand": {},
     "line": "No added loss in either feed"},
    {"id": "degraded_telecom", "name": "Degraded telecom",
     "degradation": {"telecom": {"p_missing": 0.25, "p_oscillation": 0.20}}, "demand": {},
     "line": "Signalling: 25% missing, 20% oscillation"},
    {"id": "degraded_fare", "name": "Degraded fare feed",
     "degradation": {"atto": {"p_untapped": 0.28}}, "demand": {},
     "line": "Fare taps: 28% not recorded"},
    {"id": "peak_plus_50", "name": "Peak demand +50%", "degradation": {},
     "demand": {"peak_multiplier": 1.5}, "line": "Morning and evening peak demand × 1.5 (assumption)"},
    {"id": "stress_both", "name": "Both feeds degraded",
     "degradation": {"telecom": {"p_missing": 0.25, "p_oscillation": 0.20},
                     "atto": {"p_untapped": 0.28}}, "demand": {},
     "line": "Signalling 25% missing and fare taps 28% not recorded"},
]
PRESET_BY_ID = {p["id"]: p for p in PRESETS}

DEMAND_SCALES = [1.0, 2.0, 3.0]          # scenario multipliers; 3.0 is the protocol's central value

# --- Step-1 plan conditions ----------------------------------------------------
BUDGET_STOPS = [
    {"idx": 0, "tco_factor": 1.125, "label": "+12.5%"},
    {"idx": 1, "tco_factor": 1.250, "label": "+25%"},
    {"idx": 2, "tco_factor": 1.375, "label": "+37.5%"},
]
DELTA_STOPS = [
    {"idx": 0, "delta": 0.10, "label": "10%"},
    {"idx": 1, "delta": 0.20, "label": "20%"},
    {"idx": 2, "delta": 0.30, "label": "30%"},
    {"idx": 3, "delta": 999.0, "label": "No limit"},
]
DEFAULT_BUDGET_IDX = 1
DEFAULT_DELTA_IDX = 1
DEFAULT_SCALE = 3.0

# Fare-feed quality levels for the conditions check (realised tap loss).
QUALITY_STOPS = [
    {"idx": 0, "p_untapped": 0.00, "label": "0%"},
    {"idx": 1, "p_untapped": 0.28, "label": "28%"},
    {"idx": 2, "p_untapped": 0.50, "label": "50%"},
]


# --- capability detection --------------------------------------------------
def sumo_available() -> bool:
    return bool(shutil.which("sumo")) and bool(os.environ.get("SUMO_HOME")) and \
        (Path(os.environ["SUMO_HOME"]) / "tools" / "randomTrips.py").exists()


def capabilities() -> dict:
    """What this machine can do, reported verbatim. A missing dependency disables
    the step that needs it; nothing falls back to an archived result."""
    caps = {"generator": False, "optimiser": False, "simulation": False, "notes": []}
    install_import_paths()
    try:
        import pandas  # noqa: F401
        (GEN_DIR / "output" / "persons.parquet").resolve(strict=True)
        (GEN_DIR / "output" / "trips.parquet").resolve(strict=True)
        caps["generator"] = True
    except Exception as exc:                                  # pragma: no cover
        caps["notes"].append(f"Data generation unavailable: {exc}")
    try:
        import pymoo  # noqa: F401
        caps["optimiser"] = True
    except Exception as exc:                                  # pragma: no cover
        caps["notes"].append(f"Optimiser unavailable: {exc}")
    if sumo_available():
        caps["simulation"] = True
    else:
        caps["notes"].append("SUMO or SUMO_HOME/tools not found — simulation is unavailable "
                             "on this machine.")
    return caps
