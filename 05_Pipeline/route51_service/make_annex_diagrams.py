"""Annex B and Annex C diagrams (supervisor review, finding A5).

Both annexes carried bracketed text placeholders instead of figures. The content was
fully specified in those placeholders; this script draws it. Vector-quality PNG at
300 dpi, plus SVG sources archived alongside so the "source archived" claim is true.

Outputs (output/, or --output-dir): figB1_architecture.png/.svg, figC1_pipeline.png/.svg.
Labels follow release route51_v2_20260922 (analytical fusion, cost cap, headway spread,
registered 30 + 30 SUMO runs).
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUT = Path(__file__).resolve().parent / "output"

INK = "#1b2733"
MUTED = "#5b6b7c"
LAYER_FILL = ["#eef3f8", "#e6eef7", "#dfe9f4", "#d8e4f1", "#d1dfee", "#cadaeb"]
ACCENT = "#1f4e9c"
WARM = "#c2410c"


def box(ax, x, y, w, h, text, fill="#ffffff", edge=MUTED, fs=7.4, bold=False,
        radius=0.012, lw=0.9, tc=INK):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle=f"round,pad=0,rounding_size={radius}",
                                linewidth=lw, edgecolor=edge, facecolor=fill, zorder=2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            color=tc, zorder=3, fontweight="bold" if bold else "normal",
            linespacing=1.35)


def arrow(ax, p1, p2, color=ACCENT, lw=1.3, style="-|>", rad=0.0, ls="-"):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=11,
                                 linewidth=lw, color=color, zorder=4,
                                 linestyle=ls,
                                 connectionstyle=f"arc3,rad={rad}"))


# ===================================================================== Annex B
def architecture():
    fig, ax = plt.subplots(figsize=(9.6, 7.4))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")

    LAYERS = [
        ("Layer 5 — Decision support & TMC integration",
         ["City dashboard", "TMC / TSP interface", "Gap-analysis report"]),
        ("Layer 4 — Simulation & evaluation",
         ["SUMO microsimulation", "Baseline vs plan\n(30 + 30 runs, protocol v1.0)", "KPI extraction"]),
        ("Layer 3 — AI optimisation engine",
         ["NSGA-II (pymoo)\npop 120 × 300 gen", "Objectives: wait, cost,\ncrowding disutility",
          "Constraints: cost cap,\nheadway spread δ, fleet"]),
        ("Layer 2 — Demand estimation (fusion)",
         ["Telecom OD matrix", "Proportional fusion\n(telecom prior + ATTO)", "Alighting inference\n(trip-chaining)"]),
        ("Layer 1 — Ingestion & governance",
         ["Secure API gateway", "Anonymisation check\nk-anonymity, k = 10",
          "H3 indexing\n(res 7 / res 9)"]),
        ("Layer 0 — External data sources",
         ["Telecom CDR / signalling\nLAU · HO · TA", "ATTO tap-in\n(no alighting)",
          "Bus GPS telemetry", "OSM / GTFS\nstatic reference"]),
    ]

    top, bot = 0.920, 0.045
    n = len(LAYERS)
    gap = 0.016
    lh = (top - bot - gap * (n - 1)) / n

    y = top - lh
    band_y = []
    for i, (title, items) in enumerate(LAYERS):
        band_y.append(y)
        ax.add_patch(FancyBboxPatch((0.055, y), 0.90, lh,
                                    boxstyle="round,pad=0,rounding_size=0.012",
                                    linewidth=1.0, edgecolor="#9fb3c8",
                                    facecolor=LAYER_FILL[i], zorder=1))
        ax.text(0.068, y + lh - 0.028, title, ha="left", va="center",
                fontsize=8.6, fontweight="bold", color=ACCENT, zorder=3)
        k = len(items)
        bw = 0.80 / k - 0.018
        for j, it in enumerate(items):
            bx = 0.088 + j * (bw + 0.018)
            box(ax, bx, y + 0.012, bw, lh - 0.052, it)
        y -= lh + gap

    # upward data flow
    for i in range(n - 1, 0, -1):
        y_from = band_y[i] + lh
        y_to = band_y[i - 1]
        arrow(ax, (0.505, y_from), (0.505, y_to), lw=1.5)

    # feedback Layer 4 -> Layer 3, routed outside the bands so nothing overlaps
    y4 = band_y[1] + lh * 0.35
    y3 = band_y[2] + lh * 0.65
    # The label sits to the LEFT of the arc, on an opaque patch, so it cannot collide
    # with the curve (N6 of the v8 review).
    arrow(ax, (0.962, y4), (0.962, y3), color=WARM, lw=1.6, rad=-0.85)
    ax.text(0.944, (y4 + y3) / 2, "realised KPIs → re-optimisation trigger",
            ha="center", va="center", fontsize=6.6, color=WARM, rotation=270,
            bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.92),
            zorder=7)

    # Operator / planner trust boundary. It sits between Layer 0 and Layer 1: §2.3.2
    # has aggregation happen inside the operator's environment, so Layer 1 already
    # receives pre-aggregated indicators. Layer 0 is therefore below the line.
    yb = band_y[5] + lh + gap / 2
    ax.plot([0.035, 0.975], [yb, yb], ls=(0, (5, 3)), lw=1.3, color=WARM, zorder=6)
    ax.text(0.972, yb + 0.009,
            "planning-authority environment — aggregated indicators only",
            ha="right", va="bottom", fontsize=6.9, color=WARM, style="italic", zorder=6)
    ax.text(0.972, yb - 0.009,
            "operator environment — in the design, raw records stay below this line",
            ha="right", va="top", fontsize=6.9, color=WARM, style="italic", zorder=6)

    ax.text(0.5, 0.985, "Figure B.1  Six-layer AI-based system architecture",
            ha="center", va="top", fontsize=10.6, fontweight="bold", color=INK)
    ax.text(0.5, 0.955, "Tashkent Line 51 corridor — data flow upward, KPI feedback from "
                        "Layer 4 to Layer 3",
            ha="center", va="top", fontsize=7.8, color=MUTED)

    fig.savefig(OUT / "figB1_architecture.png", dpi=300, bbox_inches="tight",
                facecolor="white")
    fig.savefig(OUT / "figB1_architecture.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("  figB1_architecture.png / .svg")


# ===================================================================== Annex C
def pipeline():
    fig, ax = plt.subplots(figsize=(9.6, 7.0))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")

    def band(y, h, label, fill):
        ax.add_patch(FancyBboxPatch((0.045, y), 0.91, h,
                                    boxstyle="round,pad=0,rounding_size=0.012",
                                    linewidth=1.0, edgecolor="#9fb3c8",
                                    facecolor=fill, zorder=1))
        ax.text(0.058, y + h - 0.022, label, ha="left", va="center",
                fontsize=8.4, fontweight="bold", color=ACCENT, zorder=3)

    # row 1 — raw inputs
    band(0.775, 0.155, "Raw inputs", LAYER_FILL[0])
    raw = ["LAU / HO / TA\n500 m – 5 km\nhourly, by day type",
           "ATTO tap-in\nstop-level, no alighting",
           "Bus GPS\n30 s pings, lat/lon, speed",
           "OSM / GTFS\nstops, routes, roads"]
    for j, t in enumerate(raw):
        box(ax, 0.068 + j * 0.222, 0.792, 0.202, 0.093, t)

    # row 2 — processing
    band(0.585, 0.155, "Layer 1–2 processing", LAYER_FILL[2])
    proc = ["H3 aggregation\n→ hourly OD counts\nk-anonymity k = 10",
            "Trip-chaining\n→ inferred alighting",
            "GPS calibration\n→ ground truth"]
    for j, t in enumerate(proc):
        box(ax, 0.085 + j * 0.293, 0.602, 0.270, 0.093, t)

    # single fusion node
    box(ax, 0.270, 0.478, 0.46, 0.072,
        "Analytical fusion\nATTO boardings × (telecom prior + trip chains)",
        fill="#eaf1fb", edge=ACCENT, bold=True, fs=7.8)

    # optimisation
    band(0.285, 0.155, "Layer 3 optimisation", LAYER_FILL[3])
    box(ax, 0.085, 0.302, 0.38, 0.093,
        "NSGA-II\nminimise waiting · cost index · crowding (pax·min)")
    box(ax, 0.520, 0.302, 0.40, 0.093,
        "subject to\ncost cap ×1.25 · headway spread δ · fleet norm")

    # outputs
    band(0.135, 0.120, "Layer 3 output", LAYER_FILL[4])
    box(ax, 0.130, 0.152, 0.33, 0.070, "Executable timetable\n(rounded to whole trips)")
    box(ax, 0.530, 0.152, 0.33, 0.070, "Capacity swap plan\n12 m ↔ 18 m")

    # evaluation + decision
    box(ax, 0.085, 0.028, 0.40, 0.078,
        "Layer 4 — SUMO, both directions\nbaseline vs plan, 30 + 30 runs",
        fill="#eaf1fb", edge=ACCENT, fs=7.6)
    box(ax, 0.520, 0.028, 0.40, 0.078,
        "Layer 5 — decision report\nadjust · swap · escalate to TMC",
        fill="#eaf1fb", edge=ACCENT, fs=7.6)

    for a, b in [((0.5, 0.775), (0.5, 0.740)),
                 ((0.5, 0.585), (0.5, 0.550)),
                 ((0.5, 0.478), (0.5, 0.440)),
                 ((0.5, 0.285), (0.5, 0.255)),
                 ((0.29, 0.135), (0.29, 0.106)),
                 ((0.70, 0.135), (0.70, 0.106))]:
        arrow(ax, a, b, lw=1.5)
    arrow(ax, (0.485, 0.067), (0.520, 0.067), lw=1.5)

    ax.text(0.5, 0.985, "Figure C.1  Data pipeline: from raw signals to capacity "
                        "recommendations",
            ha="center", va="top", fontsize=10.6, fontweight="bold", color=INK)

    fig.savefig(OUT / "figC1_pipeline.png", dpi=300, bbox_inches="tight",
                facecolor="white")
    fig.savefig(OUT / "figC1_pipeline.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("  figC1_pipeline.png / .svg")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Draw the Annex B and C diagrams.")
    ap.add_argument("--output-dir", type=Path, default=OUT)
    OUT = ap.parse_args().output_dir
    OUT.mkdir(parents=True, exist_ok=True)
    print("Annex diagrams:")
    architecture()
    pipeline()
