"""Layer 4 — background traffic generator (SUMO randomTrips.py + duarouter).

Generates seed-dependent background car traffic on the corridor for a time window,
so bus travel time reflects congestion (closing the free-flow optimism gap) and,
crucially, varies from replication to replication. Requires SUMO_HOME to point at
the SUMO install (its tools/ holds randomTrips.py).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def sumo_home():
    home = os.environ.get("SUMO_HOME")
    if home and Path(home, "tools", "randomTrips.py").exists():
        return home
    try:
        import sumolib  # noqa: F401
        guess = Path(sumolib.__file__).resolve().parents[1] / "sumo"
        if (guess / "tools" / "randomTrips.py").exists():
            os.environ.setdefault("SUMO_HOME", str(guess))
            return str(guess)
    except Exception:
        pass
    return None


class BackgroundError(RuntimeError):
    """Background traffic was required but could not be generated."""


def generate_background(net, wd, seed, begin, end, period, fringe_factor=15, strict=False):
    """Write cars_{seed}.rou.xml of routed background traffic; return its path or None.

    period = seconds between successive vehicle insertions (smaller -> more traffic).
    With strict=True (protocol runs) a missing SUMO_HOME/tools or a failing tool
    raises BackgroundError instead of silently returning None.
    """
    home = sumo_home()
    if home is None:
        if strict:
            raise BackgroundError("SUMO_HOME/tools/randomTrips.py not found; background traffic is required")
        return None
    wd = Path(wd).resolve()
    net = Path(net).resolve()
    trips = wd / f"trips_{seed}.xml"
    cars = wd / f"cars_{seed}.rou.xml"
    rt = Path(home) / "tools" / "randomTrips.py"
    try:
        subprocess.run([sys.executable, str(rt), "-n", str(net), "-o", str(trips),
                        "-b", str(begin), "-e", str(end), "-p", str(period),
                        "--seed", str(seed), "--fringe-factor", str(fringe_factor)],
                       check=True, capture_output=True, text=True, cwd=wd)
        subprocess.run(["duarouter", "-n", str(net), "-r", str(trips), "-o", str(cars),
                        "--ignore-errors", "true", "--no-warnings", "true"],
                       check=True, capture_output=True, text=True, cwd=wd)
    except subprocess.CalledProcessError as e:
        if strict:
            raise BackgroundError(f"background generation failed: {(e.stdout or '')[-400:]} {(e.stderr or '')[-400:]}")
        return None
    if not cars.exists():
        if strict:
            raise BackgroundError("duarouter produced no route file")
        return None
    return cars
