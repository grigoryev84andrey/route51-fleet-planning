"""Export of one planning result (plan S18).

One export = one new directory (timestamp + random suffix; two exports in the
same second never overwrite each other) with:

  report.html              readable page, print-ready, system fonts only
  report.md                the same content as plain text
  decision_record.json     every number at full precision, statuses, fingerprints
  dispatch_baseline.csv    exact trips of the baseline timetable
  dispatch_plan.csv        exact trips of the selected plan (what the simulation used)
  schedule_by_period.csv   aggregated view of the plan
  <stem>.zip               all of the above

JSON keeps numbers unrounded; only HTML/Markdown format them. The status shown
is the one the shared validator returned; an export without an applicable
simulation says so and carries no borrowed figures.
"""
from __future__ import annotations

import csv
import html
import json
import secrets
import time
import zipfile
from pathlib import Path

from settings import EXPORTS_DIR

LIMITATIONS = [
    "Synthetic methods demonstration — not an empirical claim about real Line 51.",
    "Demand scale, crowding threshold and multiplier, capacities and relative costs are scenario assumptions.",
    "Every trip of a period is modelled as an average vehicle; queues and denied boarding are not modelled.",
    "Fleet availability not verified: the vehicle check is normative, not a vehicle-block schedule.",
    "The simulation measures bus full-trip duration of the 07:00–10:00 departures, not passengers' in-vehicle time.",
    "The headway-spread limit constrains variation between periods; it is not a spatial-equity measure.",
]


REASONS = {
    "no_simulation": "no simulation of this plan",
    "simulation_not_registered": "simulated in this session, but not a registered confirmatory series",
    "simulation_not_applicable": "the simulation does not match this plan or is incomplete",
    "not_carried_over_from_morning_simulation": "the morning simulation is not carried over to the whole day",
    "no_sourced_rate": "no sourced money rate per vehicle-km",
    "ivt_not_instrumented": "passenger in-vehicle time was not measured",
}


def _reason(code) -> str:
    return REASONS.get(code, code or "unknown")


def _n(v, d=0):
    if v is None:
        return "—"
    return f"{v:,.{d}f}".replace(",", " ")


def _pct(new, old):
    if new is None or old in (None, 0):
        return "—"
    x = 100.0 * (new - old) / old
    return f"{'+' if x > 0 else '−' if x < 0 else ''}{abs(x):.1f}%"


def _esc(s) -> str:
    return html.escape(str(s))


def _emphasize(text: str, benefit: bool, html_format: bool = False) -> str:
    text = _esc(text) if html_format else text
    if not benefit:
        return text
    return f"<strong>{text}</strong>" if html_format else f"**{text}**"


def export_state(sim: dict | None) -> tuple[str, str]:
    if sim is None:
        return "model_only", "Not simulated for this plan"
    if sim.get("completion_status") != "complete":
        return "incomplete_simulation", sim.get("status_label", "Incomplete run — no confirmatory conclusion")
    if sim.get("registered"):
        return "confirmatory", sim.get("status_label")
    return "exploratory_simulation", sim.get("status_label")


def comparison_rows(bundle: dict) -> list[tuple]:
    b = bundle["baseline"]["dispatch_metrics"]
    p = bundle["plan"]["dispatch_metrics"]
    return [("Waiting", "pax·min/day", b["wait"], p["wait"], 0),
            ("Crowding disutility", "pax·min/day", b["crowd_min"], p["crowd_min"], 0),
            ("Waiting + crowding", "pax·min/day", b["gen_cost"], p["gen_cost"], 0),
            ("Operating-cost index", "relative veh-km", b["cost"], p["cost"], 1),
            ("Longest headway", "min", max(b["effective_headways"].values()),
             max(p["effective_headways"].values()), 1),
            ("Vehicles needed (normative)", "vehicles", b["fleet_needed"], p["fleet_needed"], 0),
            ("Highest load / full capacity", "—", b["max_phi"], p["max_phi"], 2)]


def _sim_lines(sim: dict | None, html_format: bool = False) -> list[str]:
    if sim is None:
        return ["Not simulated for this plan."]
    part = lambda text, bold=False: _emphasize(str(text), bold, html_format)
    L = [f"Status: {part(sim.get('status_label'), True)} "
         f"(evidence: {part(sim.get('evidence_kind'))}; "
         f"effect: {part(sim.get('effect_status'))})."]
    pr = sim.get("primary") or {}
    if pr.get("defined"):
        current = part(f"{pr['baseline_mean']:.2f} min", True)
        planned = part(f"{pr['candidate_mean']:.2f} min", True)
        L.append(f"Mean full-trip bus duration, 07:00–10:00 departures, both directions: current "
                 f"{current}, plan {planned}; difference "
                 f"(current − plan) {pr['delta_min']:+.2f} min, 95% CI {pr['ci95'][0]:+.2f} … "
                 f"{pr['ci95'][1]:+.2f}; runs {pr['n_baseline']} + {pr['n_candidate']}.")
    if sim.get("evidence_note"):
        L.append(part(sim["evidence_note"]))
    if sim.get("invalid_runs"):
        L.append(f"{len(sim['invalid_runs'])} run(s) invalid — no confirmatory conclusion.")
    return L


def _eco_lines(eco: dict | None, wait_weight: int = 1, html_format: bool = False) -> list[str]:
    if eco is None:
        return ["Not calculated."]
    part = lambda text, bold=False: _emphasize(str(text), bold, html_format)
    money = lambda value, bold=False: part(f"{_n(value / 1e6)} million UZS/year", bold)
    key = f"wait_weight_{wait_weight}"
    am = eco["scopes"]["am_peak"]["by_wait_weight"][key]
    day = eco["scopes"]["all_day_model"]["by_wait_weight"][key]
    lines = [
        f"Value of time: {part(_n(eco['value_of_time']['value_uzs_per_pax_h']), True)} "
        f"UZS per passenger-hour ({part(eco['value_of_time']['reference_period'])}); "
        f"{part(eco['days_per_year']['value'])} days per year (scenario assumption)."
    ]
    for sc, b in (("am_peak", am), ("all_day_model", day)):
        ivt = (f"not included — {part(_reason(b['passenger_with_ivt_reason']))}"
               if b["passenger_with_ivt_uzs"] is None else
               money(b["ivt_benefit_uzs"], sc == "am_peak" and b["ivt_benefit_uzs"] > 0))
        operator = ("unknown — no sourced money rate per vehicle-km"
                    if b["operator_delta_cost_uzs"] is None else
                    money(b["operator_delta_cost_uzs"], sc == "all_day_model"))
        lines.append(
            f"[{sc}] Estimated passenger benefit from waiting and crowding (model): "
            f"{money(b['passenger_model_uzs'], b['passenger_model_uzs'] > 0)} "
            f"(waiting {part(_n(b['wait_benefit_h']), b['wait_benefit_h'] > 0)} h/year; "
            f"crowding {part(_n(b['crowd_benefit_h']), b['crowd_benefit_h'] > 0)} h/year; "
            "positive = benefit); "
            f"(sensitivity to value of time ±30%: "
            f"{money(b['passenger_model_uzs_vot_minus_30'], sc == 'am_peak')}–"
            f"{money(b['passenger_model_uzs_vot_plus_30'], sc == 'am_peak')} — not a confidence interval); "
            f"in-vehicle time: {ivt}; operator money cost: {operator}; "
            f"change in 12 m-equivalent km: "
            f"{part(_n(b['delta_equiv_12m_km_year']), sc == 'all_day_model')} per year.")
    ra = eco["resources"]
    lines.append(
        f"{part('[Operator & fleet, whole day]')} Vehicle-km per day: current "
        f"{part(_n(ra['baseline']['all']['veh_km']))}, plan "
        f"{part(_n(ra['candidate']['all']['veh_km']), True)} "
        f"(18 m: {part(_n(ra['candidate']['all']['veh_km_18m']), True)}); "
        "normative vehicle-hours per day: current "
        f"{part(_n(ra['baseline']['all']['normative_veh_h'], 1))}, plan "
        f"{part(_n(ra['candidate']['all']['normative_veh_h'], 1), True)}. "
        "Fleet availability not verified.")
    threshold = day["rate_threshold_model_only"]
    rate = (f"{part(_n(threshold['value']), True)} UZS per 12 m-equivalent km — "
            if threshold["value"] is not None else "")
    combined = (f"unknown — {part(_reason(day['combined_reason']))}"
                if day["combined_uzs"] is None else money(day["combined_uzs"], True))
    lines.append(f"[Monetisation] Combined balance: {combined}; money rate at which the "
                 f"whole-day model balance stays non-negative: {rate}{part(threshold['text'])}. "
                 "Not a profit or payback figure.")
    lines.append("All-day result with simulated time: not evaluated.")
    return lines


def render_markdown(bundle: dict) -> str:
    state, label = export_state(bundle.get("simulation"))
    c = bundle["front"]["conditions"]
    L = [f"# Route 51 — planning result ({label})",
         f"Exported {bundle['generated_at']} · plan {bundle['plan']['id']} · "
         f"demand scale ×{c['scale']} · maximum operating-cost increase {c['budget_label']} · "
         f"maximum headway spread {c['delta_label']}",
         "", "## Plan", "| Period | Baseline headway | Plan headway (effective) | 18 m trips / all |",
         "|---|---|---|---|"]
    for r in bundle["plan_table"]["rows"]:
        L.append(f"| {r['label']} | {r['baseline_headway_min']:.1f} min | "
                 f"{r['plan_effective_headway_min']:.2f} min | "
                 f"{r['trips_18m_per_direction']} / {r['trips_per_direction']} |")
    L += ["", "## Model comparison on the executable timetable", "| Measure | Unit | Baseline | Plan | Change |",
          "|---|---|---|---|---|"]
    for name, unit, b, p, d in comparison_rows(bundle):
        better = b is not None and p is not None and p < b
        L.append(f"| {name} | {unit} | {_n(b, d)} | {_emphasize(_n(p, d), better)} | "
                 f"{_emphasize(_pct(p, b), better)} |")
    L += ["", "Bold values mark improvements and key economics inputs and outcomes. Positive passenger "
          f"benefits are model estimates, not operator profit. Economics below uses a waiting-time weight of {bundle.get('wait_weight', 1)}×."]
    L += ["", "## Simulation"] + [f"- {x}" for x in _sim_lines(bundle.get("simulation"))]
    L += ["", "## Economics"] + [f"- {x}" for x in _eco_lines(bundle.get("economics"), bundle.get("wait_weight", 1))]
    L += ["", "## Limitations"] + [f"- {x}" for x in LIMITATIONS]
    L += ["", f"Fingerprints: input `{bundle['fingerprints']['input']}`, plan "
               f"`{bundle['plan']['plan_fingerprint']}`, protocol `{bundle['fingerprints']['protocol']}`."]
    return "\n".join(L) + "\n"


_CSS = """
:root{--ink:#1d1f20;--mut:#4d5257;--line:#c9ccd0;--bg:#fff;--ok:#2f5a3d;--warn:#6b4f12}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,-apple-system,Arial,sans-serif;padding:16px}
.wrap{max-width:920px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:22px 0 6px;border-bottom:1px solid var(--line);padding-bottom:3px}
.meta{color:var(--mut);font-size:13px}
.badge{display:inline-block;border:1px solid var(--ink);padding:2px 8px;font-size:12px;margin:6px 0}
.tbl{overflow-x:auto} table{border-collapse:collapse;width:100%;min-width:480px;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:right;vertical-align:top}
th:first-child,td:first-child{text-align:left}
thead th{font-size:12px;color:var(--mut);font-weight:600}
li{margin:3px 0}
strong{font-weight:700}
@media print{body{padding:0}}
"""


def render_html(bundle: dict) -> str:
    state, label = export_state(bundle.get("simulation"))
    c = bundle["front"]["conditions"]
    plan_rows = "".join(
        f"<tr><td>{_esc(r['label'])}</td><td>{r['baseline_headway_min']:.1f}</td>"
        f"<td>{r['plan_effective_headway_min']:.2f}</td><td>{r['trips_18m_per_direction']} / "
        f"{r['trips_per_direction']}</td></tr>" for r in bundle["plan_table"]["rows"])
    cmp_rows = "".join(
        f"<tr><td>{_esc(n)}</td><td>{_esc(u)}</td><td>{_n(b, d)}</td>"
        f"<td>{_emphasize(_n(p, d), b is not None and p is not None and p < b, True)}</td>"
        f"<td>{_emphasize(_pct(p, b), b is not None and p is not None and p < b, True)}</td></tr>"
        for n, u, b, p, d in comparison_rows(bundle))
    sim = "".join(f"<li>{x}</li>" for x in _sim_lines(bundle.get("simulation"), html_format=True))
    eco = "".join(f"<li>{x}</li>" for x in _eco_lines(bundle.get("economics"), bundle.get("wait_weight", 1), html_format=True))
    lim = "".join(f"<li>{_esc(x)}</li>" for x in LIMITATIONS)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Route 51 planning result</title><style>{_CSS}</style></head><body><div class="wrap">
<h1>Route 51 — planning result</h1>
<div class="badge" role="status">{_esc(label)}</div>
<p class="meta">Exported {_esc(bundle['generated_at'])} · plan {_esc(bundle['plan']['id'])}
({_esc(bundle['plan']['label'])}) · demand scale ×{c['scale']} · maximum operating-cost increase
{_esc(c['budget_label'])} · maximum headway spread across periods {_esc(c['delta_label'])} ·
synthetic data</p>
<h2>Plan</h2><div class="tbl"><table><thead><tr><th>Period</th><th>Baseline headway, min</th>
<th>Plan headway (effective), min</th><th>18 m trips / all, per direction</th></tr></thead>
<tbody>{plan_rows}</tbody></table></div>
<h2>Model comparison on the executable timetable</h2>
<div class="tbl"><table><thead><tr><th>Measure</th><th>Unit</th><th>Baseline</th><th>Plan</th>
<th>Change</th></tr></thead><tbody>{cmp_rows}</tbody></table></div>
<p class="meta">Bold values mark improvements and key economics inputs and outcomes. Positive passenger
benefits are model estimates, not operator profit. Economics below uses a waiting-time weight of {bundle.get('wait_weight', 1)}×.</p>
<p class="meta">Recommended for the selected priorities: minimum waiting + crowding disutility among
plans that stay within the limits after rounding to whole trips.</p>
<h2>Simulation</h2><ul>{sim}</ul>
<h2>Economics</h2><ul>{eco}</ul>
<h2>Limitations</h2><ul>{lim}</ul>
<p class="meta">Fingerprints — input {_esc(bundle['fingerprints']['input'])}; plan
{_esc(bundle['plan']['plan_fingerprint'])}; protocol {_esc(bundle['fingerprints']['protocol'])}.</p>
</div></body></html>"""


def _write_trips(path: Path, trips) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(trips[0].as_dict().keys()))
        w.writeheader()
        for t in sorted(trips, key=lambda t: (t.planned_dep_s, t.trip_id)):
            w.writerow(t.as_dict())


def write_export(bundle: dict, trips_baseline, trips_plan) -> dict:
    state, label = export_state(bundle.get("simulation"))
    stem = f"route51_result_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}_{state}"
    outdir = Path(EXPORTS_DIR) / stem
    outdir.mkdir(parents=True, exist_ok=False)
    files = []
    (outdir / "report.html").write_text(render_html(bundle), encoding="utf-8")
    (outdir / "report.md").write_text(render_markdown(bundle), encoding="utf-8")
    record = dict(bundle, export_state=state, export_label=label, limitations=LIMITATIONS)
    (outdir / "decision_record.json").write_text(json.dumps(record, indent=2, ensure_ascii=False,
                                                            default=str), encoding="utf-8")
    _write_trips(outdir / "dispatch_baseline.csv", trips_baseline)
    _write_trips(outdir / "dispatch_plan.csv", trips_plan)
    with open(outdir / "schedule_by_period.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["period", "baseline_headway_min", "plan_headway_min", "plan_effective_headway_min",
                    "trips_per_direction", "trips_18m_per_direction", "share_18m_desired", "share_18m_actual"])
        for r in bundle["plan_table"]["rows"]:
            w.writerow([r["period"], r["baseline_headway_min"], r["plan_headway_min"],
                        r["plan_effective_headway_min"], r["trips_per_direction"],
                        r["trips_18m_per_direction"], r["share_18m_desired"], r["share_18m_actual"]])
    for name in ("report.html", "report.md", "decision_record.json", "dispatch_baseline.csv",
                 "dispatch_plan.csv", "schedule_by_period.csv"):
        files.append(outdir / name)
    zip_path = Path(EXPORTS_DIR) / f"{stem}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, arcname=f"{stem}/{f.name}")
    return {"stem": stem, "state": state, "label": label,
            "files": [{"name": f.name, "bytes": f.stat().st_size,
                       "url": f"/api/export/file/{stem}/{f.name}"} for f in files],
            "zip_url": f"/api/export/zip/{zip_path.name}",
            "primary_url": f"/api/export/file/{stem}/report.html"}
