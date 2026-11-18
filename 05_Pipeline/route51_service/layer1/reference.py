"""Static reference band for Layer 1.

Loads the canonical stop catalog, the published H3 grid (from the generator's
corridor_cells.geojson — the proper layered handoff; rebuilt from stops if the
file is absent), and the fleet catalog. Incoming dynamic feeds are validated
against this reference (stop ids must exist, H3 cells classified in/out of grid).
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import h3


@dataclass
class Reference:
    stops: dict[str, tuple[float, float]]      # stop_id -> (lat, lon)
    stop_cells: dict[str, str]                 # stop_id -> H3 cell
    grid: set[str]                             # corridor H3 cells (the hex grid)
    fleet: dict[str, int]                      # vehicle_type -> capacity
    res: int

    def in_grid(self, cell: str) -> bool:
        return cell in self.grid


def _load_stops(path: Path, res: int):
    stops, cells = {}, {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            sid = row["id"].strip()
            lat, lon = float(row["lat"]), float(row["lon"])
            stops[sid] = (lat, lon)
            cells[sid] = h3.latlng_to_cell(lat, lon, res)
    return stops, cells


def _load_grid(geojson_path: Path, stop_cells: dict[str, str], res: int) -> set[str]:
    """Prefer the published grid; fall back to a buffer around stop cells."""
    if geojson_path.exists():
        gj = json.loads(geojson_path.read_text(encoding="utf-8"))
        cells = {f["properties"]["h3"] for f in gj["features"] if "h3" in f.get("properties", {})}
        if cells:
            return cells
    # Fallback: 1-ring buffer around each stop cell.
    grid: set[str] = set()
    for c in stop_cells.values():
        grid |= set(h3.grid_disk(c, 1))
    return grid


def load_reference(cfg) -> Reference:
    res = cfg["spatial"]["h3_resolution"]
    root = Path(cfg["_root"])
    stops, stop_cells = _load_stops(root / cfg["paths"]["stops_csv"], res)
    grid = _load_grid(root / cfg["paths"]["h3_grid_geojson"], stop_cells, res)
    return Reference(
        stops=stops, stop_cells=stop_cells, grid=grid,
        fleet=dict(cfg["fleet_catalog"]), res=res,
    )
