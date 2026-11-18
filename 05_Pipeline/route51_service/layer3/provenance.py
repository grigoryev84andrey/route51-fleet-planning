"""Fingerprints and versions shared by the scripts, the prototype and the export.

One place decides what identifies the inputs, a plan and the model, so the
service scripts and the console reach the same fingerprints for the same thing.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import result_contracts as rc

from .dispatch import DISPATCH_VERSION
from .operations import CROWDING_MODEL_VERSION

_HERE = Path(__file__).resolve().parent
_MODEL_FILES = ("operations.py", "dispatch.py", "optimize.py")

SCIENTIFIC_CONFIG_KEYS = ("operations", "crowding", "fleet_catalog", "simulation")


def _file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def model_versions() -> dict:
    import numpy
    import pymoo
    v = {"schema_version": rc.SCHEMA_VERSION,
         "crowding_model_version": CROWDING_MODEL_VERSION,
         "dispatch_version": DISPATCH_VERSION,
         "numpy": numpy.__version__, "pymoo": pymoo.__version__}
    for f in _MODEL_FILES:
        v[f"sha256:{f}"] = _file_sha(_HERE / f)
    return v


def scientific_config(cfg: dict) -> dict:
    """The resolved configuration blocks that change scientific results."""
    return {k: cfg[k] for k in SCIENTIFIC_CONFIG_KEYS if k in cfg}


def input_fp(fused_unscaled, cfg: dict) -> str:
    return rc.input_fingerprint(fused_unscaled, scientific_config(cfg))


def plan_fp(trips) -> str:
    """Executable plan of one condition: the ordered trip list. The condition label
    prefix of the trip id is left out, so the same timetable has one fingerprint."""
    rows = [[t.trip_id.split(".", 1)[1], t.direction, t.period, int(t.planned_dep_s), t.vtype, int(t.capacity)]
            for t in sorted(trips, key=lambda t: (t.planned_dep_s, t.trip_id))]
    return rc.plan_fingerprint({"trips": rows})


def model_fp() -> str:
    return rc.model_fingerprint(model_versions())
