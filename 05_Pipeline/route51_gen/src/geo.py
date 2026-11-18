"""Geographic substrate for the Route-51 corridor.

Builds the internal H3 cell set (a buffered corridor around the 23-stop
polyline), snaps the D10 attraction anchors and metro-gateway cordon zones to
H3, and exposes helpers (distance, a placeholder cell-density surface) that the
downstream demand engine and the telecom degradation model consume.

All distances are haversine metres (a travel-time proxy until the SUMO network
provides real edge travel times). The per-cell density surface is a PLACEHOLDER
(uniform or a NE->centre gradient) because true dasymetric downscaling needs OSM
landuse=residential / building footprints, which are not yet available
(D10 6.1 — this is the load-bearing step, flagged not faked).
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

import h3


# --------------------------------------------------------------------------- #
# Basic geometry
# --------------------------------------------------------------------------- #
EARTH_R_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


def _interp(lat1, lon1, lat2, lon2, step_m: float):
    """Yield points roughly every step_m along a great-circle segment."""
    d = haversine_m(lat1, lon1, lat2, lon2)
    n = max(1, int(d // step_m))
    for i in range(n + 1):
        t = i / n
        yield (lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t)


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #
@dataclass
class Stop:
    id: str
    name: str
    lat: float
    lon: float
    cell: str = ""


@dataclass
class Corridor:
    res: int
    stops: list[Stop]
    internal_cells: set[str]
    anchor_cells: dict[str, str]          # anchor_id -> cell
    anchor_internal: dict[str, bool]      # anchor_id -> in corridor?
    cordon_cells: dict[str, str]          # cordon zone id -> representative cell (gateways only)
    density: dict[str, float] = field(default_factory=dict)  # cell -> relative residential weight

    @property
    def n_internal(self) -> int:
        return len(self.internal_cells)


# --------------------------------------------------------------------------- #
# Loaders / builders
# --------------------------------------------------------------------------- #
def load_stops(csv_path: str | Path, res: int) -> list[Stop]:
    """Load the canonical semicolon-delimited stop file (S01..S23)."""
    stops: list[Stop] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        for row in reader:
            lat = float(row["lat"].strip())
            lon = float(row["lon"].strip())
            cell = h3.latlng_to_cell(lat, lon, res)
            stops.append(Stop(row["id"].strip(), row["name"].strip(), lat, lon, cell))
    return stops


def _buffer_k(res: int, buffer_m: float) -> int:
    """grid_disk ring count approximating a metric buffer at this resolution."""
    edge = h3.average_hexagon_edge_length(res, unit="m")
    return max(1, math.ceil(buffer_m / edge))


def build_corridor(
    stops_csv: str | Path,
    res: int,
    buffer_m: float,
    anchors: list[dict],
    cordon_zones: list[dict],
    density_model: str = "uniform_corridor",
) -> Corridor:
    """Construct the buffered H3 corridor and snap anchors + gateways."""
    stops = load_stops(stops_csv, res)
    k = _buffer_k(res, buffer_m)

    # Corridor = union of grid_disk(k) over densely interpolated points along the
    # consecutive-stop polyline. ~50 m interpolation guarantees no gaps between
    # adjacent stop cells even where stops are far apart.
    internal: set[str] = set()
    for a, b in zip(stops[:-1], stops[1:]):
        for lat, lon in _interp(a.lat, a.lon, b.lat, b.lon, step_m=50.0):
            internal |= set(h3.grid_disk(h3.latlng_to_cell(lat, lon, res), k))

    # Anchors -> cells; mark whether each lands inside the corridor.
    anchor_cells, anchor_internal = {}, {}
    for an in anchors:
        cell = h3.latlng_to_cell(an["lat"], an["lon"], res)
        anchor_cells[an["id"]] = cell
        anchor_internal[an["id"]] = cell in internal

    # Cordon gateways (Pakhtakor / Novza) get a representative cell; aggregate
    # cordon zones are abstract (no geometry) and handled in the demand engine.
    cordon_cells = {}
    for cz in cordon_zones:
        if cz.get("kind") == "metro_gateway":
            cordon_cells[cz["id"]] = h3.latlng_to_cell(cz["lat"], cz["lon"], res)

    corridor = Corridor(
        res=res,
        stops=stops,
        internal_cells=internal,
        anchor_cells=anchor_cells,
        anchor_internal=anchor_internal,
        cordon_cells=cordon_cells,
    )
    corridor.density = _density_surface(corridor, density_model)
    return corridor


def _density_surface(corr: Corridor, model: str) -> dict[str, float]:
    """PLACEHOLDER residential-density weight per internal cell.

    'uniform_corridor' -> equal weight (1.0).
    'position_gradient' -> higher toward the Yunusabad (home) end, lower toward
        the central (attraction) end. Crude stand-in for the real NE residential
        mass; clearly labelled placeholder. Real model = dasymetric on OSM
        landuse (D10 6.1).
    """
    if model == "uniform_corridor":
        return {c: 1.0 for c in corr.internal_cells}

    if model == "position_gradient":
        # Project each cell onto the home(S23)->centre(S01) axis; weight ~ closeness to home end.
        home = corr.stops[-1]   # S23 Yunusabad Block 6
        cen = corr.stops[0]     # S01 Beshagach
        axis = haversine_m(home.lat, home.lon, cen.lat, cen.lon) or 1.0
        out = {}
        for c in corr.internal_cells:
            lat, lon = h3.cell_to_latlng(c)
            d_home = haversine_m(lat, lon, home.lat, home.lon)
            out[c] = max(0.1, 1.0 - d_home / axis)  # 1.0 at home end -> ~0.1 at centre
        return out

    raise ValueError(f"unknown density model: {model}")


def cells_to_geojson(corr: Corridor) -> dict:
    """Minimal GeoJSON FeatureCollection of corridor cells (for QA / map view)."""
    feats = []
    for c in corr.internal_cells:
        ring = h3.cell_to_boundary(c)  # [(lat, lon), ...]
        coords = [[lon, lat] for lat, lon in ring] + [[ring[0][1], ring[0][0]]]
        feats.append({
            "type": "Feature",
            "properties": {"h3": c, "density": round(corr.density.get(c, 0.0), 3)},
            "geometry": {"type": "Polygon", "coordinates": [coords]},
        })
    return {"type": "FeatureCollection", "features": feats}
