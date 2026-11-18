"""Layer 5 — supporting documents rendered from the decision record (no computation).

  * render_equity_matrix       -> the headway-spread options as a policy table;
  * render_implementation_spec -> conditions that must hold before any pilot, and
                                  what would have to be monitored.
"""
from __future__ import annotations


def _n(v, d=0):
    return "—" if v is None else f"{v:,.{d}f}".replace(",", " ")


def render_equity_matrix(rec: dict) -> str:
    so = rec["headway_spread_options"]
    base = so["baseline_gen_cost_relaxed"]
    L = ["# Headway-spread options — Route 51 (synthetic model)",
         "*The cap on how much headways may differ between periods is a policy choice. "
         f"{so['basis']}. Units: pax·min/day.*",
         "\n| Cap | Waiting | Crowding | Waiting + crowding | vs baseline | Cost index |",
         "|---|---|---|---|---|---|"]
    for o in so["options"]:
        lab = "none" if o["delta"] is None else f"{o['delta']:.2f}"
        if not o["feasible"]:
            L.append(f"| {lab} | — | — | infeasible | — | — |")
            continue
        vs = f"{100 * (o['gen_cost_relaxed'] - base) / base:+.1f}%" if base else "—"
        L.append(f"| {lab} | {_n(o['wait_relaxed'])} | {_n(o['crowd_min_relaxed'])} | "
                 f"{_n(o['gen_cost_relaxed'])} | {vs} | {_n(o['cost_relaxed'], 1)} |")
    L.append("\n**Reading the table.** A tighter cap forces more uniform headways across the day. "
             "Whether that is worth its cost in waiting and crowding is for the operator and the "
             "policymaker to decide; the model only prices the options. The cap does not measure "
             "spatial equity, and a cap can be met by lengthening short headways as well as by "
             "shortening long ones.")
    L.append("\n*Figures rest on synthetic data and a labelled demand scenario.*")
    return "\n".join(L) + "\n"


def render_implementation_spec(rec: dict) -> str:
    L = ["# Conditions before any pilot — Route 51",
         "*This project does not establish operational readiness. The list below states what "
         "would have to be true, and measured, before a pilot could be considered.*",
         "\n## 1. Preconditions",
         "- Calibrate demand on real ATTO / telecom data for the corridor and period.",
         "- Replace scenario parameters (crowding θ and λ, capacities, relative costs, demand scale) "
         "with sourced values.",
         "- Build vehicle blocks and duties for the plan; the current fleet check is normative only "
         "(operational feasibility: unknown).",
         "- Model queues and denied boarding if loads above full capacity remain.",
         "- Validate simulated running times against AVL/GPS data.",
         "\n## 2. What a pilot would monitor",
         "| Measure | Source | Why |",
         "|---|---|---|",
         "| Headway adherence at terminals | AVL / GPS | regularity of the new timetable |",
         "| Load at the busiest segment by trip | on-board counts, ATTO | crowding assumptions |",
         "| Waiting at stops | headways, surveys | the waiting increase the plan accepts |",
         "| Cycle time by period | GPS | vehicle requirement |",
         "| Operating cost | operator accounts | budget cap |",
         "\n## 3. Limitations carried from the model"]
    for x in rec["limitations"]:
        L.append(f"- {x}")
    return "\n".join(L) + "\n"
