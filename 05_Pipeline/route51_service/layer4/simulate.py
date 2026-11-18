"""Layer 4 — run SUMO headless and parse outputs into metrics.

Metrics:
  * realized one-way travel time (overall and per period) and its running vs dwell
    split (dwell is endogenous to demand; running is free-flow -> optimistic);
  * headway regularity (coefficient of variation of headways) at an upstream vs a
    downstream stop, to show irregularity propagation along the corridor.
"""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd


def write_cfg(wd, rou, cfgname, end_s):
    cfg = f"""<configuration>
 <input>
  <net-file value="corridor.net.xml"/>
  <route-files value="{rou}"/>
  <additional-files value="stops.add.xml"/>
 </input>
 <time><begin value="0"/><end value="{end_s}"/></time>
 <output>
  <tripinfo-output value="tripinfo.xml"/>
  <stop-output value="stopinfo.xml"/>
 </output>
 <processing><time-to-teleport value="-1"/></processing>
 <report><no-step-log value="true"/><duration-log.disable value="true"/></report>
</configuration>"""
    (Path(wd) / cfgname).write_text(cfg)


def run_sumo(wd, cfgname):
    r = subprocess.run(["sumo", "-c", cfgname], cwd=str(wd), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stdout + r.stderr)[-1200:])


def _cv(times):
    t = np.sort(np.asarray(times, float))
    if t.size < 3:
        return float("nan")
    h = np.diff(t)
    return float(h.std() / h.mean()) if h.mean() > 0 else float("nan")


def parse(wd, stops):
    wd = Path(wd)
    trip = ET.parse(wd / "tripinfo.xml").getroot()
    rows = []
    for e in trip.findall("tripinfo"):
        vid = e.get("id")
        rows.append({"period": vid.split(".")[0],
                     "tt_s": float(e.get("duration")),
                     "wait_s": float(e.get("waitingTime")),
                     "stop_s": float(e.get("stopTime"))})
    tt = pd.DataFrame(rows)

    # Headway regularity at an upstream and a downstream stop.
    up, dn = f"bs_{stops[1]}", f"bs_{stops[-2]}"
    arr = {up: [], dn: []}
    for e in ET.parse(wd / "stopinfo.xml").getroot().findall("stopinfo"):
        bs = e.get("busStop")
        if bs in arr:
            arr[bs].append(float(e.get("started")))
    return tt, _cv(arr[up]), _cv(arr[dn]), trip
