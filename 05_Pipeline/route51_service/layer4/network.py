"""Layer 4 — SUMO corridor network builder.

Builds a synthetic linear SUMO network for the 23-stop corridor directly from the
stop coordinates (no OSM dependency): one node per stop, one-lane edges between
consecutive stops with real inter-stop lengths, and a bus stop at each stop. The
inter-stop running speed is a CALIBRATION parameter — with no background traffic
or signals, SUMO free-flow running time is a lower bound (optimism bias).
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path


def _xy(la, lo, lat0, lon0):
    return ((lo - lon0) * math.cos(math.radians(lat0)) * 111320.0,
            (la - lat0) * 110540.0)


def build_network(stops, stop_ll, link_speed_kmh, wd, lanes=1):
    wd = Path(wd)
    wd.mkdir(parents=True, exist_ok=True)
    lat0, lon0 = stop_ll[stops[0]]
    coords = {s: _xy(*stop_ll[s], lat0, lon0) for s in stops}
    speed = link_speed_kmh / 3.6
    edges = [f"e_{a}_{b}" for a, b in zip(stops[:-1], stops[1:])]

    nod = ["<nodes>"] + [f'  <node id="{s}" x="{coords[s][0]:.1f}" y="{coords[s][1]:.1f}"/>'
                         for s in stops] + ["</nodes>"]
    edg = ["<edges>"]
    for (a, b), eid in zip(zip(stops[:-1], stops[1:]), edges):
        L = math.dist(coords[a], coords[b])
        edg.append(f'  <edge id="{eid}" from="{a}" to="{b}" numLanes="{lanes}" '
                   f'speed="{speed:.2f}" length="{L:.1f}"/>')
    edg.append("</edges>")
    (wd / "n.nod.xml").write_text("\n".join(nod))
    (wd / "n.edg.xml").write_text("\n".join(edg))

    net = wd / "corridor.net.xml"
    subprocess.run(["netconvert", "--node-files", str(wd / "n.nod.xml"),
                    "--edge-files", str(wd / "n.edg.xml"),
                    "--output-file", str(net), "--no-turnarounds", "true"],
                   check=True, capture_output=True, text=True)

    add = ["<additional>"]
    for i, s in enumerate(stops[:-1]):
        add.append(f'  <busStop id="bs_{s}" lane="{edges[i]}_0" startPos="5" endPos="20"/>')
    add.append(f'  <busStop id="bs_{stops[-1]}" lane="{edges[-1]}_0" startPos="-20" endPos="-1"/>')
    add.append("</additional>")
    (wd / "stops.add.xml").write_text("\n".join(add))
    return net, edges


DIRECTIONS = ("fwd", "rev")


def build_network_bidir(stops, stop_ll, link_speed_kmh, wd, lanes=2):
    """Two directional corridors between the same stop nodes (protocol route51_v2).

    Forward edges e_<a>_<b> follow the canonical stop order, reverse edges
    r_<b>_<a> run back. Every direction has its own bus stops (bs_f_<stop>,
    bs_r_<stop>) on its own lane 0, with the same straight-line segment lengths
    as layer3.operations.segment_lengths_m. Returns (net path, {dir: edges},
    {dir: [(stop, busStop id)]}).
    """
    wd = Path(wd)
    wd.mkdir(parents=True, exist_ok=True)
    lat0, lon0 = stop_ll[stops[0]]
    coords = {s: _xy(*stop_ll[s], lat0, lon0) for s in stops}
    speed = link_speed_kmh / 3.6
    seq = {"fwd": list(stops), "rev": list(stops)[::-1]}
    prefix = {"fwd": "e", "rev": "r"}
    edges = {d: [f"{prefix[d]}_{a}_{b}" for a, b in zip(seq[d][:-1], seq[d][1:])] for d in DIRECTIONS}

    nod = ["<nodes>"] + [f'  <node id="{s}" x="{coords[s][0]:.1f}" y="{coords[s][1]:.1f}"/>'
                         for s in stops] + ["</nodes>"]
    edg = ["<edges>"]
    for d in DIRECTIONS:
        for (a, b), eid in zip(zip(seq[d][:-1], seq[d][1:]), edges[d]):
            L = math.dist(coords[a], coords[b])
            edg.append(f'  <edge id="{eid}" from="{a}" to="{b}" numLanes="{lanes}" '
                       f'speed="{speed:.2f}" length="{L:.1f}"/>')
    edg.append("</edges>")
    (wd / "n.nod.xml").write_text("\n".join(nod))
    (wd / "n.edg.xml").write_text("\n".join(edg))

    net = wd / "corridor.net.xml"
    subprocess.run(["netconvert", "--node-files", str(wd / "n.nod.xml"),
                    "--edge-files", str(wd / "n.edg.xml"),
                    "--output-file", str(net), "--no-turnarounds", "true"],
                   check=True, capture_output=True, text=True)

    add = ["<additional>"]
    bstops = {}
    for d in DIRECTIONS:
        tag = "f" if d == "fwd" else "r"
        bstops[d] = []
        for i, s in enumerate(seq[d][:-1]):
            bid = f"bs_{tag}_{s}"
            add.append(f'  <busStop id="{bid}" lane="{edges[d][i]}_0" startPos="5" endPos="20"/>')
            bstops[d].append((s, bid))
        last = seq[d][-1]
        bid = f"bs_{tag}_{last}"
        add.append(f'  <busStop id="{bid}" lane="{edges[d][-1]}_0" startPos="-20" endPos="-1"/>')
        bstops[d].append((last, bid))
    add.append("</additional>")
    (wd / "stops.add.xml").write_text("\n".join(add))
    return net, edges, bstops
