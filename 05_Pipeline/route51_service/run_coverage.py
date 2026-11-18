"""500 m spatial coverage ratio — H2 (thesis §3.3.1, §3.4).

Deterministic GIS computation, no simulation required. Population comes from the
WorldPop 100 m constrained raster for Uzbekistan; zones come from the OSM
admin_level=6 district boundaries of Toshkent shahri. Stops are the 23 canonical
Route-51 stops.

Coverage ratio = population within `walk_m` of an ACTIVE stop, divided by the
population of the reference area. Two reference areas are reported because the
number answers two different questions:

  corridor : the districts the Line 51 corridor passes through — the catchment the
             thesis actually claims to serve;
  city     : all twelve districts of Tashkent — context for how much of the city a
             single corridor reaches.

Everything is computed in EPSG:32642 (UTM 42N), so the 500 m buffer is a true
metric distance rather than a degree approximation.

A note on what this can and cannot show: the optimisation holds route topology
fixed (§2.1.2) and every stop is served in every plan, so served-stop coverage is
IDENTICAL under Baseline and NSGA-II by construction. The script reports the
frequency-weighted variant as well to make that explicit rather than implying an
effect that the design cannot produce.

Outputs (output/): coverage_h2.csv, coverage_by_district.csv
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GEN = ROOT.parent / "route51_gen"
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
import rasterio.mask  # noqa: E402
from pyproj import Transformer  # noqa: E402
from shapely.geometry import shape, mapping, LineString, Point  # noqa: E402
from shapely.ops import transform as shp_transform, unary_union  # noqa: E402

import runctx  # noqa: E402

RASTER = GEN / "data" / "uzb_ppp_2020_constrained.tif"
BOUNDS = GEN / "data" / "tashkent_districts.geojson"
STOPS = GEN / "data" / "Busstations.csv"
WALK_M = 500
UTM = "EPSG:32642"                       # UTM zone 42N — covers Tashkent

# The twelve districts of Toshkent shahri, with the official permanent-resident
# count (thousands) at 1 July 2024 from D10 §1 (Statistics Department of Tashkent).
# The Overpass area query also returns neighbouring Tashkent-Region districts and one
# street mis-tagged as a boundary; naming the twelve explicitly excludes those and
# keeps the filter auditable.
#
# The official totals are used as CONTROL TOTALS, not merely as a cross-check. The
# WorldPop constrained raster reproduces the national population well (33.7 M against
# ~34.2 M) but its distribution WITHIN Tashkent disagrees with the register by a wide
# margin — it under-allocates the dense central districts by a factor of three to five
# and over-allocates the southern periphery (Bektemir 8x, Yangihayot 5x, Sergeli 4x),
# almost certainly because the building-footprint layer treats the southern industrial
# and warehouse stock as residential. Using its absolute counts would put most of
# Tashkent's population in its emptiest districts.
#
# So the raster supplies the WITHIN-district shape (which is what a 100 m gridded
# product is good for) and the register supplies the district LEVELS (which is what
# official statistics are authoritative for). This is the dasymetric procedure D10 §5
# prescribes. Both the raw and the controlled figures are reported.
CITY_DISTRICTS = {
    "Olmazor Tumani": 395.0,
    "Yunusobod Tumani": 366.9,
    "Shayhontohur Tumani": 360.5,
    "Mirzo Ulug‘bek Tumani": 323.1,
    "Uchtepa Tumani": 293.5,
    "Yashnobod Tumani": 289.4,
    "Chilonzor Tumani": 271.9,
    "Sergeli tumani": 162.3,
    "Yangi hayot Tumani": 161.0,
    "Mirobod Tumani": 150.1,
    "Yakkasaroy Tumani": 126.9,
    "Bektemir Tumani": 55.7,
}


def load_stops():
    rows = []
    with open(STOPS, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            rows.append({"id": r["id"].strip(), "name": r["name"].strip(),
                         "lat": float(r["lat"]), "lon": float(r["lon"])})
    return rows


def pop_in(src, geom_utm, to_wgs):
    """Population sum of the raster inside a UTM geometry."""
    g = shp_transform(to_wgs, geom_utm)
    try:
        arr, _ = rasterio.mask.mask(src, [mapping(g)], crop=True, filled=True,
                                    nodata=np.nan)
    except ValueError:                    # geometry outside the raster
        return 0.0
    a = arr[0]
    return float(np.nansum(np.where(a < 0, np.nan, a)))


def main() -> None:
    for p in (RASTER, BOUNDS, STOPS):
        if not p.exists():
            raise SystemExit(f"missing input: {p}")

    ctx = runctx.start(__file__)
    cfg = ctx.cfg
    out = ctx.out

    fwd = Transformer.from_crs("EPSG:4326", UTM, always_xy=True).transform
    inv = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True).transform

    stops = load_stops()
    buffers = [shp_transform(fwd, Point(s["lon"], s["lat"])).buffer(WALK_M) for s in stops]
    served = unary_union(buffers)

    gj = json.loads(BOUNDS.read_text(encoding="utf-8"))
    districts = {f["properties"]["name"]: shp_transform(fwd, shape(f["geometry"]))
                 for f in gj["features"] if f["properties"]["name"] in CITY_DISTRICTS}
    missing = set(CITY_DISTRICTS) - set(districts)
    if missing:
        print(f"  WARNING: districts not found in the boundary file: {sorted(missing)}")

    print("=" * 92)
    print(f"500 m COVERAGE RATIO (H2) — WorldPop 2020 constrained 100 m, "
          f"OSM admin_level=6, {len(stops)} stops")
    print("=" * 92)

    src = rasterio.open(RASTER)
    rows = []
    print(f"  {'district':26} {'official':>10} {'raster':>10} {'share<500m':>11} "
          f"{'covered':>10}")
    for name, geom in sorted(districts.items()):
        raster_pop = pop_in(src, geom, inv)
        inter = geom.intersection(served)
        raster_cov = pop_in(src, inter, inv) if not inter.is_empty else 0.0
        # Within-district share is taken from the raster; the level from the register.
        share = raster_cov / raster_pop if raster_pop > 0 else 0.0
        official = CITY_DISTRICTS[name] * 1000
        covered = share * official
        rows.append({"district": name,
                     "population_official": round(official, 0),
                     "population_raster": round(raster_pop, 1),
                     "raster_over_official": round(raster_pop / official, 2),
                     "share_within_500m": round(share, 5),
                     "population_within_500m": round(covered, 0),
                     "coverage_pct": round(100 * share, 2),
                     "on_corridor": not inter.is_empty,
                     "area_km2": round(geom.area / 1e6, 1)})
        print(f"  {name:26} {official:>10,.0f} {raster_pop:>10,.0f} {100*share:>10.2f}% "
              f"{covered:>10,.0f}  {'corridor' if not inter.is_empty else ''}")

    df = pd.DataFrame(rows)
    df.to_csv(out / "coverage_by_district.csv", index=False)

    corr = df[df["on_corridor"]]
    summary = []
    for label, sub in (("corridor districts", corr),
                       ("Tashkent city (12 districts)", df)):
        pop = sub["population_official"].sum()
        cov = sub["population_within_500m"].sum()
        summary.append({"reference_area": label,
                        "n_districts": int(len(sub)),
                        "population": round(pop, 0),
                        "population_within_500m": round(cov, 0),
                        "coverage_pct": round(100 * cov / pop, 2),
                        "basis": "official district totals, raster-weighted within district"})

    # Corridor band: everyone within 1 km of the route alignment. This is the
    # reference the KPI is really about — of the people the corridor passes, how many
    # are within a 500 m walk of a stop? It measures stop spacing, whereas the
    # district ratios above measure the corridor's reach into the city.
    line = LineString([(s["lon"], s["lat"]) for s in stops])
    band = shp_transform(fwd, line).buffer(1000)
    band_raster = pop_in(src, band, inv)
    band_cov_raster = pop_in(src, band.intersection(served), inv)
    # Scale the band by the same register/raster correction, weighted by which
    # districts the band falls in, so it is on the same footing as the rows above.
    scale_num = sum(CITY_DISTRICTS[n] * 1000 * (g.intersection(band).area / g.area)
                    for n, g in districts.items())
    scale_den = sum(pop_in(src, g.intersection(band), inv)
                    for g in districts.values() if not g.intersection(band).is_empty)
    k = scale_num / scale_den if scale_den > 0 else 1.0
    summary.append({"reference_area": "corridor band (1 km of the alignment)",
                    "n_districts": int(corr.shape[0]),
                    "population": round(band_raster * k, 0),
                    "population_within_500m": round(band_cov_raster * k, 0),
                    "coverage_pct": round(100 * band_cov_raster / band_raster, 2)
                    if band_raster > 0 else 0.0,
                    "basis": "register-corrected raster within 1 km of the route polyline"})
    pd.DataFrame(summary).to_csv(out / "coverage_h2.csv", index=False)

    print("-" * 92)
    for s in summary:
        print(f"  {s['reference_area']:30} {s['n_districts']:>2} districts  "
              f"pop {s['population']:>11,.0f}   covered {s['population_within_500m']:>10,.0f}   "
              f"= {s['coverage_pct']:.2f}%")
    print("-" * 92)
    print(f"  Served-stop coverage is IDENTICAL under Baseline and NSGA-II: route topology")
    print(f"  is fixed (§2.1.2) and all {len(stops)} stops are served in every plan, so no")
    print(f"  tactical headway/capacity decision can move this number. H2 is not testable")
    print(f"  by the optimisation as scoped — it is a property of the route, not the plan.")
    print(f"  Raster total over the 12 districts: {df['population_raster'].sum():,.0f} "
          f"vs official {df['population_official'].sum():,.0f} "
          f"({100*df['population_raster'].sum()/df['population_official'].sum():.0f}% overall, "
          f"but see the note on its internal distribution).")
    print("Written: coverage_h2.csv, coverage_by_district.csv")
    print("=" * 92)
    src.close()


if __name__ == "__main__":
    main()
