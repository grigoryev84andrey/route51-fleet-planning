"""Fetch Tashkent administrative boundaries from OpenStreetMap via Overpass.

Pulls the 12 city districts (admin_level=8) and, where mapped, the mahalla level
(admin_level=9), and writes both as GeoJSON polygons into data/. These are the
zone geometries the 500 m coverage ratio (H2) needs; the population weights come
from the WorldPop raster alongside them.

Overpass returns ways with geometry that must be stitched into rings, so the
relation members are assembled here rather than trusted to arrive ordered.

Outputs (data/): tashkent_districts.geojson, tashkent_mahallas.geojson (if present)
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]

# Toshkent shahri (the city, not the surrounding region) is OSM relation 2216724 and
# is itself admin_level=4; its twelve districts (tuman) are mapped at admin_level=6,
# NOT 8 as the usual city convention would suggest. Mahalla, where mapped at all,
# appear at 9-10.
CITY_REL = 2216724

QUERY = """
[out:json][timeout:300];
rel(%d);
map_to_area->.city;
(
  rel(area.city)["boundary"="administrative"]["admin_level"="%d"];
);
out geom;
"""


def overpass(level: int) -> dict:
    body = urllib.parse.urlencode({"data": QUERY % (CITY_REL, level)}).encode()
    last = None
    for url in ENDPOINTS:
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    url, data=body,
                    headers={"User-Agent": "thesis-coverage-analysis/1.0"})
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.loads(r.read().decode("utf-8"))
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
                last = e
                print(f"    {url} attempt {attempt+1} failed: {e}")
                time.sleep(8 * (attempt + 1))
    raise SystemExit(f"Overpass unreachable: {last}")


def rings_from_relation(rel: dict) -> tuple[list, list]:
    """Stitch member ways into closed rings. Returns (outer, inner) coordinate rings."""
    def stitch(ways):
        segs = [list(w) for w in ways if w]
        rings = []
        while segs:
            cur = segs.pop(0)
            changed = True
            while changed and cur[0] != cur[-1]:
                changed = False
                for i, s in enumerate(segs):
                    if s[0] == cur[-1]:
                        cur += s[1:]; segs.pop(i); changed = True; break
                    if s[-1] == cur[-1]:
                        cur += s[::-1][1:]; segs.pop(i); changed = True; break
                    if s[-1] == cur[0]:
                        cur = s[:-1] + cur; segs.pop(i); changed = True; break
                    if s[0] == cur[0]:
                        cur = s[::-1][:-1] + cur; segs.pop(i); changed = True; break
            if len(cur) >= 4 and cur[0] == cur[-1]:
                rings.append(cur)
        return rings

    outer_ways, inner_ways = [], []
    for m in rel.get("members", []):
        if m.get("type") != "way" or "geometry" not in m:
            continue
        coords = [(round(p["lon"], 7), round(p["lat"], 7)) for p in m["geometry"]]
        (inner_ways if m.get("role") == "inner" else outer_ways).append(coords)
    return stitch(outer_ways), stitch(inner_ways)


def to_geojson(data: dict, level: int) -> dict:
    feats = []
    for el in data.get("elements", []):
        if el.get("type") != "relation":
            continue
        outer, inner = rings_from_relation(el)
        if not outer:
            continue
        t = el.get("tags", {})
        polys = [[[list(pt) for pt in ring]] for ring in outer]
        for ring in inner:                       # attach holes to the first outer ring
            polys[0].append([list(pt) for pt in ring])
        geom = ({"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1
                else {"type": "MultiPolygon", "coordinates": polys})
        feats.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "osm_id": el["id"],
                "name": t.get("name"),
                "name_en": t.get("name:en"),
                "name_ru": t.get("name:ru"),
                "admin_level": t.get("admin_level"),
            },
        })
    return {"type": "FeatureCollection",
            "name": f"tashkent_admin_level_{level}",
            "crs": {"type": "name",
                    "properties": {"name": "urn:ogc:def:crs:OGC:1.3/CRS84"}},
            "features": feats}


def main() -> None:
    DATA.mkdir(exist_ok=True)
    for level, fname in ((6, "tashkent_districts.geojson"),
                         (9, "tashkent_mahallas.geojson"),
                         (10, "tashkent_mahallas_l10.geojson")):
        print(f"admin_level={level} ...")
        gj = to_geojson(overpass(level), level)
        n = len(gj["features"])
        if n == 0:
            print(f"  no admin_level={level} relations mapped — skipping {fname}")
            continue
        path = DATA / fname
        path.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
        print(f"  {n} polygons -> {path.name} ({path.stat().st_size/1024:.0f} KB)")
        for f in gj["features"][:14]:
            p = f["properties"]
            print(f"     {p['name']}  |  {p.get('name_en') or ''}")


if __name__ == "__main__":
    main()
