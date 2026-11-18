"""Stage-0 foundation check.

Builds the H3 corridor from the real 23 stops, snaps the D10 anchors and the
two metro gateways, and prints a verification report so the calibration wiring
can be sanity-checked before the demand engine and the three feeds are built.
Writes a corridor GeoJSON for visual QA.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import h3  # noqa: E402

from config import load_config  # noqa: E402
from geo import build_corridor, cells_to_geojson, haversine_m  # noqa: E402


def main() -> None:
    cfg = load_config(ROOT / "config.yaml")
    sa = cfg.study_area
    data_dir = Path(cfg.run.data_dir)

    corr = build_corridor(
        stops_csv=data_dir / sa.stops_csv,
        res=sa.h3_resolution,
        buffer_m=sa.catchment_buffer_m,
        anchors=cfg["attraction_anchors"],
        cordon_zones=sa["cordon_zones"],
        density_model=cfg["population"]["home_density_model"],
    )

    # Route length from the polyline.
    stops = corr.stops
    route_m = sum(
        haversine_m(a.lat, a.lon, b.lat, b.lon) for a, b in zip(stops[:-1], stops[1:])
    )
    edge_m = h3.average_hexagon_edge_length(sa.h3_resolution, unit="m")
    cell_area = h3.cell_area(stops[0].cell, unit="km^2")

    print("=" * 68)
    print("ROUTE-51 CORRIDOR — Stage-0 geo verification")
    print("=" * 68)
    print(f"Stops loaded            : {len(stops)}  ({stops[0].id} {stops[0].name}"
          f"  ->  {stops[-1].id} {stops[-1].name})")
    print(f"Canonical direction     : {sa.canonical_direction}")
    print(f"Polyline length         : {route_m/1000:.1f} km")
    print(f"H3 resolution           : {sa.h3_resolution}  (edge ~{edge_m:.0f} m, "
          f"cell ~{cell_area:.3f} km^2)")
    print(f"Catchment buffer        : {sa.catchment_buffer_m} m  "
          f"-> grid_disk k inferred from edge")
    print(f"Internal corridor cells : {corr.n_internal}")
    print(f"Density model           : {cfg['population']['home_density_model']} (PLACEHOLDER)")

    # Are all stops inside the corridor? (sanity: they must be)
    stops_in = sum(1 for s in stops if s.cell in corr.internal_cells)
    print(f"Stops inside corridor   : {stops_in}/{len(stops)}  "
          f"({'OK' if stops_in == len(stops) else 'CHECK'})")

    print("-" * 68)
    print("ATTRACTION ANCHORS  (in-corridor = on-route pull; external = off-route)")
    for an in cfg["attraction_anchors"]:
        aid = an["id"]
        loc = "INTERNAL" if corr.anchor_internal[aid] else "external"
        # nearest stop distance for context
        cell_lat, cell_lon = h3.cell_to_latlng(corr.anchor_cells[aid])
        dmin = min(haversine_m(cell_lat, cell_lon, s.lat, s.lon) for s in stops)
        print(f"  {aid:<16} {an['class']:<12} {loc:<9} nearest stop ~{dmin:5.0f} m")

    print("-" * 68)
    print("CORDON METRO GATEWAYS")
    for cz in sa["cordon_zones"]:
        if cz.get("kind") == "metro_gateway":
            cell = corr.cordon_cells[cz["id"]]
            inside = cell in corr.internal_cells
            clat, clon = h3.cell_to_latlng(cell)
            dmin = min(haversine_m(clat, clon, s.lat, s.lon) for s in stops)
            print(f"  {cz['id']:<14} {'INTERNAL' if inside else 'external':<9} "
                  f"nearest stop ~{dmin:5.0f} m   ({cz['note']})")

    # Write corridor GeoJSON for QA.
    out = Path(cfg.run.output_dir) / "corridor_cells.geojson"
    out.write_text(json.dumps(cells_to_geojson(corr)), encoding="utf-8")
    print("-" * 68)
    print(f"Corridor GeoJSON written: {out}  ({corr.n_internal} cells)")
    n_ph = len([p for p in cfg["_placeholders"] if not p.startswith("(note)")])
    print(f"Outstanding PLACEHOLDERs : {n_ph} (see load-time warning)")
    print("=" * 68)


if __name__ == "__main__":
    main()
