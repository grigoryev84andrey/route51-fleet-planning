"""Layer 5 — render the decision record as a readable brief (no computation)."""
from __future__ import annotations


def _n(v, d=0):
    return "—" if v is None else f"{v:,.{d}f}".replace(",", " ")


def _pct(a, b):
    if a is None or b in (None, 0):
        return "—"
    return f"{100 * (a - b) / b:+.1f}%"


def _plan_table(rows):
    out = ["| Period | Baseline headway | Plan headway (effective) | 18 m trips / all, per direction |",
           "|---|---|---|---|"]
    for r in rows:
        if "plan_headway_min" in r:
            out.append(f"| {r['period']} | {r['baseline_headway_min']:.1f} min | "
                       f"{r['plan_effective_headway_min']:.2f} min | "
                       f"{r['plan_trips_18m_per_direction']} / {r['plan_trips_per_direction']} |")
        else:
            out.append(f"| {r['period']} | {r['baseline_headway_min']:.1f} min | — | — |")
    return "\n".join(out)


def _sim_lines(sim: dict) -> list[str]:
    if sim.get("value", "x") is None and sim.get("reason_code"):
        return [f"- Simulation: not available ({sim['message']})."]
    L = [f"- Status: **{sim.get('status_label', 'see analysis')}** "
         f"(evidence: {sim.get('evidence_kind')}, effect: {sim.get('effect_status')})."]
    pr = sim.get("primary")
    if pr and pr.get("delta_min") is not None:
        L.append(f"- Mean full-trip bus duration, 07:00–10:00 cohort, both directions: "
                 f"baseline {pr['baseline_mean']:.2f} min, candidate {pr['candidate_mean']:.2f} min; "
                 f"Δ = baseline − candidate = {pr['delta_min']:+.2f} min "
                 f"(95% CI {pr['ci95'][0]:+.2f} … {pr['ci95'][1]:+.2f}), n = {pr['n_baseline']} + {pr['n_candidate']}.")
        L.append("- This is bus full-trip duration, not an individual passenger's in-vehicle time.")
    return L


def render_brief(rec: dict) -> str:
    ctx = rec["context"]
    mc = rec["model_comparison"]
    L = ["# Route 51 — Planning brief (synthetic model)",
         f"*Layer 5 summary of layer-3 run `{rec.get('layer3_run_id')}`. Demand scenario "
         f"×{ctx['demand_scenario_scale']}; crowding {ctx['crowding']['model']} "
         f"(θ = {ctx['crowding']['theta']}, λ(1) = {ctx['crowding']['lambda_full']}). "
         "Results describe the model only; operational feasibility is unknown.*"]

    L.append("\n## 1. Selected plan (protocol rule)")
    L.append(f"Selected for the chosen priorities by minimum waiting + crowding disutility among "
             f"plans feasible after dispatch: **{mc.get('candidate_id') or 'none'}**.\n")
    L.append(_plan_table(rec["plan_table"]))

    L.append("\n## 2. Model comparison on the executable timetable")
    b, c = mc["baseline"], mc["candidate"]
    has_c = isinstance(c, dict) and "wait" in c
    L.append("| Measure | Baseline | Plan | Change |")
    L.append("|---|---|---|---|")
    for key, label, d in (("wait", "Waiting (pax·min/day)", 0),
                          ("crowd_min", "Crowding disutility (pax·min/day)", 0),
                          ("gen_cost", "Waiting + crowding (pax·min/day)", 0),
                          ("cost", "Operating-cost index", 1),
                          ("max_phi", "Maximum load / full capacity", 2),
                          ("headway_spread", "Headway spread across periods", 3),
                          ("fleet_needed", "Vehicles needed (normative)", 0)):
        cv = c[key] if has_c else None
        L.append(f"| {label} | {_n(b[key], d)} | {_n(cv, d)} | {_pct(cv, b[key])} |")
    L.append("\nWorsening and improvement are shown side by side; the plan trades longer waits "
             "for less crowding when waiting rises.")

    L.append("\n## 3. Simulation")
    L.extend(_sim_lines(rec["simulation"]))

    L.append("\n## 4. Economics")
    e = rec["economics"]
    if e.get("value", "x") is None and e.get("reason_code"):
        L.append(f"- Not available ({e['message']}).")
    else:
        L.append("- See `economics_result.json`; each block states its scope and status.")

    L.append("\n## 5. Headway-spread options (exploratory)")
    so = rec["headway_spread_options"]
    L.append(f"*{so['basis']}.* Baseline (relaxed) waiting + crowding: {_n(so['baseline_gen_cost_relaxed'])}.\n")
    L.append("| Cap on headway spread | Waiting + crowding (relaxed) | vs baseline |")
    L.append("|---|---|---|")
    for o in so["options"]:
        lab = "none" if o["delta"] is None else f"{o['delta']:.2f}"
        if not o["feasible"]:
            L.append(f"| {lab} | infeasible | — |")
        else:
            L.append(f"| {lab} | {_n(o['gen_cost_relaxed'])} | "
                     f"{_pct(o['gen_cost_relaxed'], so['baseline_gen_cost_relaxed'])} |")
    L.append("\nThe cap limits variation of headways between periods; choosing it is a policy "
             "decision. It is not a measure of spatial equity.")

    L.append("\n## 6. Limitations")
    for x in rec["limitations"]:
        L.append(f"- {x}")
    g = rec.get("governance", {})
    if g.get("present"):
        L.append(f"\n*Layer-1 checks on inputs ({', '.join(g.get('feeds', []))}): "
                 f"{'passed' if g.get('all_accepted') and g.get('all_pass') else 'see report'}. "
                 "These are rule checks, not proof of legal compliance.*")
    return "\n".join(L) + "\n"
