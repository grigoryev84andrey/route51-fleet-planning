"""Layer 3 — fleet resources counted on the executable trips.

Directions are summed once (every trip is one direction). Kept apart on purpose:

  veh_km            sum of trip lengths
  veh_km_18m        the part run by 18 m buses
  equiv_12m_km      km_12m + r * km_18m, r = cost_large / cost_small
  cost_index        cost_small * equiv_12m_km  (the relative operating-cost index)
  capacity_km       sum of length * full capacity (90 / 150; not seat-km)
  running_veh_h     normative running time (one-way norm per trip)
  normative_veh_h   running plus terminal layover per trip

Measured SUMO durations are a separate quantity and live in the simulation
analysis; they are not mixed into these counts.
"""
from __future__ import annotations

from .dispatch import Trip
from .operations import PERIOD_NAMES

RESOURCES_VERSION = "resources_v1"


def resources(trips: list[Trip], params: dict, *, one_way_min: float, layover_min: float,
              periods=None) -> dict:
    sel = [t for t in trips if periods is None or t.period in periods]
    cs, cl = float(params["veh_cost_small"]), float(params["veh_cost_large"])
    r = cl / cs
    km12 = sum(t.length_km for t in sel if t.vtype == "bus12")
    km18 = sum(t.length_km for t in sel if t.vtype == "bus18")
    equiv = km12 + r * km18
    return {
        "scope_periods": list(periods) if periods is not None else list(PERIOD_NAMES),
        "trips": len(sel),
        "trips_18m": sum(1 for t in sel if t.vtype == "bus18"),
        "veh_km": km12 + km18,
        "veh_km_18m": km18,
        "cost_ratio_r": r,
        "equiv_12m_km": equiv,
        "cost_index": cs * equiv,
        "capacity_km": sum(t.length_km * t.capacity for t in sel),
        "running_veh_h": len(sel) * one_way_min / 60.0,
        "normative_veh_h": len(sel) * (one_way_min + layover_min) / 60.0,
        "resources_version": RESOURCES_VERSION,
    }
