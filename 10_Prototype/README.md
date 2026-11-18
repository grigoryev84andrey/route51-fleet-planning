# Fleet Planning Console — working prototype

**Route 51 service planning · Andrey Grigorev, ITPU · September 2026**

A working front end for the Route-51 pipeline of Chapter 3, built to the design brief
in `../08_Frontend_Design/Route51_Fleet_Console_UI_v2_RU.md`. Three steps —
`Conditions → Compare plans → Validate & export` — over the shared generator,
optimiser and microsimulation, ending in a report you can export.

The thesis example can open its matching registered result from the accepted release;
new scenarios run the pipeline. The interface labels those two actions separately.

---

## Start it

```bash
python -m pip install -r requirements.txt
```

Then double-click **`run.cmd`** (Windows) or run **`./run.sh`**, and the console opens
at <http://127.0.0.1:8051>.
On Windows, `run.cmd` also finds Python 3.13 in the standard per-user installation
directory when `python` is not on `PATH`.

Read **[docs/USER_GUIDE_EN.md](docs/USER_GUIDE_EN.md)** or
**[docs/USER_GUIDE_RU.md](docs/USER_GUIDE_RU.md)** — they cover the whole path,
including the choice between **Open thesis example** and **Create a scenario**.

---

## What actually runs

| Step | Layers | What the console calls | Time |
|---|---|---|---|
| **1 Conditions** | 0 → 2 | Load and validate the frozen matrix, or generate synthetic feeds, ingest, fuse and validate them | depends on source |
| **2 Compare plans** | 3 | Optimise with three protocol seeds and check the rounded dispatch | about 20–60 s in the September 2026 checks; machine-dependent |
| **3 Validate & export** | 4, 5 | Open a matching registered result, or run a new 30 + 30 SUMO series; compute scoped economics and export | instant to several minutes |

All of it is imported from `../05_Pipeline`. The console owns no science: it is a
driver, a renderer and an exporter.

`../06_Results/tables` supplies the frozen demand matrix and the published sweeps shown
alongside the live ones. The earlier 30 + 30 replication run is read **only** through the
historical adapter: it is labelled as a historical reference with its own metric
definition, and it never confirms a plan on screen. The accepted release in
`../06_Results/releases` is read only after `active_release.json` points at it.

---

## Reproduction check

On the **Thesis matrix** setting the console reproduces the release (run
`06_Results/runs/s12_layer3_central`):

| | Release | Console |
|---|---|---|
| Selected plan | `43:1` — 10.8 / 13.0 / 10.7 / 13.0 min | same |
| 18 m share | 95 % / 0 % / 100 % / 0 % | same |
| Matrix quality (frozen pair) | cosine 0.882 · 13 of 20 | same |
| Daily boardings (×3 scenario) | 17 292 | 17 292 |
| Waiting (model) | 83 333 → 95 608 pax·min/day | same |
| Crowding disutility | 237 329 → 125 300 pax·min/day | same |
| Sum, cost index | −31.1 % · 2 252.8 → 2 752.0 | same |
| Morning valuation, waiting 1× / 2× | 5.34 / 4.16 bn UZS/yr | same |

The optimiser is stochastic only through its seeds, which are fixed; a different seed set
is reported as a different plan rather than silently compared.

On **Create a scenario** the chain is re-run live; in the documented reference run its
demand volume differed from the frozen matrix by 0.24 %. The new input has a different
fingerprint, so the registered release result cannot be attached to it.

---

## Layout

```
10_Prototype/
├── run.cmd, run.sh            start the console
├── requirements.txt
├── backend/
│   ├── server.py              HTTP service and the session's working set
│   ├── pipeline.py            the bridge to 05_Pipeline — every real call lives here
│   ├── archive.py             historical adapter for 06_Results/tables and the release
│   ├── jobs.py                background jobs with staged progress
│   ├── report.py              the exported recommendation, four formats
│   └── settings.py            paths, presets, slider stops, capability detection
├── frontend/
│   ├── index.html, app.js, styles.css
│   ├── fonts.css, fonts/      Barlow and Barlow Condensed, served locally (OFL 1.1)
├── docs/
│   ├── USER_GUIDE_EN.md       the full guide, English
│   ├── USER_GUIDE_RU.md       the full guide, Russian
│   └── ARCHITECTURE.md        how the console maps onto the pipeline
└── exports/                   generated recommendations land here
    └── route51_…_verified/    one shipped sample from the earlier protocol, kept as a
                               historical example of the export format
```

---

## Requirements

- **Python 3.13** and the packages in `requirements.txt`
- **Eclipse SUMO 1.27** on `PATH`, with `SUMO_HOME` and `tools/randomTrips.py` — required
  for a new simulation. A matching registered result can be opened without SUMO; an
  unrelated archived result is never substituted
- `05_Pipeline` and `06_Results` present as siblings of this folder

The console reports what the machine can do on start-up and disables what it cannot.

---

## The rule this prototype follows

Every figure says where it came from — computed in this session, or read from a labelled
historical file — and the exported file carries the same map.

Registered simulation results pass `result_contracts.validate_simulation_analysis`
(schema 2), which records
`input_match`, `plan_match`, `evidence_kind`, `completion_status`, `protocol_status` and
`effect_status`. A simulation of a *different* plan can never become the verification of
the plan on screen: the console reports it as a historical reference and offers to run one
that does describe the plan. That check is the difference between a demo and a tool.
