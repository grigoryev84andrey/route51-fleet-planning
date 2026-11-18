"""Layer 5 — static results page (self-contained HTML).

Shows precomputed results only: the plan table, the model comparison on the
executable timetable, and the headway-spread options. The earlier client-side
re-implementation of the operations model was removed: a second copy of the
formulas could drift from the model that produced the numbers.
"""
from __future__ import annotations

import html
import json


def _n(v, d=0):
    return "—" if v is None else f"{v:,.{d}f}".replace(",", " ")


def build_dashboard(rec: dict) -> str:
    mc = rec["model_comparison"]
    b, c = mc["baseline"], mc["candidate"]
    has_c = isinstance(c, dict) and "wait" in c
    rows = "".join(
        f"<tr><th scope='row'>{html.escape(lab)}</th><td>{_n(b[k], d)}</td>"
        f"<td>{_n(c[k], d) if has_c else '—'}</td></tr>"
        for k, lab, d in (("wait", "Waiting, pax·min/day", 0),
                          ("crowd_min", "Crowding disutility, pax·min/day", 0),
                          ("gen_cost", "Waiting + crowding, pax·min/day", 0),
                          ("cost", "Operating-cost index", 1),
                          ("max_phi", "Maximum load / full capacity", 2),
                          ("fleet_needed", "Vehicles needed (normative)", 0)))
    plan = "".join(
        f"<tr><th scope='row'>{html.escape(r['period'])}</th><td>{r['baseline_headway_min']:.1f}</td>"
        f"<td>{_n(r.get('plan_effective_headway_min'), 2)}</td>"
        f"<td>{r.get('plan_trips_18m_per_direction', '—')} / {r.get('plan_trips_per_direction', '—')}</td></tr>"
        for r in rec["plan_table"])
    lim = "".join(f"<li>{html.escape(x)}</li>" for x in rec["limitations"])
    data = html.escape(json.dumps(rec, ensure_ascii=False, default=str))
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Route 51 — model results</title>
<style>
body{{margin:0;padding:16px;font:15px/1.5 system-ui,Segoe UI,Arial,sans-serif;background:#fff;color:#1b1f24}}
h1{{font-size:20px}} h2{{font-size:16px;margin-top:24px}}
.wrap{{max-width:900px;margin:0 auto}}
.tbl{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;min-width:420px}}
th,td{{border-bottom:1px solid #d5d9de;padding:6px 8px;text-align:right}} th[scope=row],thead th:first-child{{text-align:left}}
.note{{color:#4a525c;font-size:13px}}
</style></head><body><div class="wrap">
<h1>Route 51 — model results (synthetic)</h1>
<p class="note">Layer-3 run {html.escape(str(rec.get('layer3_run_id')))} · selected plan
{html.escape(str(mc.get('candidate_id')))} · operational feasibility: unknown.</p>
<h2>Plan</h2><div class="tbl"><table><thead><tr><th>Period</th><th>Baseline headway, min</th>
<th>Plan effective headway, min</th><th>18 m trips / all, per direction</th></tr></thead><tbody>{plan}</tbody></table></div>
<h2>Model comparison on the executable timetable</h2>
<div class="tbl"><table><thead><tr><th>Measure</th><th>Baseline</th><th>Plan</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>Limitations</h2><ul>{lim}</ul>
<script type="application/json" id="record">{data}</script>
</div></body></html>"""
