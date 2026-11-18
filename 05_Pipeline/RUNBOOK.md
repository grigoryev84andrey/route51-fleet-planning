# RUNBOOK — running the pipeline, stage by stage

Authoritative instructions for the technical component. Every stage below lists what it
needs, what it writes, and roughly how long it takes. Run them in the order given: each
stage consumes the previous one's output.

Timings are from the reference machine (Windows 11, Python 3.13.13, SUMO 1.27.0). Stages
marked *measured* were timed on a clean re-run of this archive; the rest are estimates.

This runbook describes release `route51_v2_20260922`. Every stage of Part B and Part C is
started through `runctx`, which takes `--config`, `--output-dir`, `--run-id`,
`--input-dir`, `--input NAME=PATH`, `--protocol` and `--legacy-output`, resolves the inputs
before creating the run directory, and writes `run_meta.json` (script hash, argv, effective
configuration and its hash, library versions, timings). The commands below are the ones
that produced the release; the run directories they name are in `06_Results/runs/`.

---

## Before you start

**1. Check the tools.**

```bash
python --version
```

```bash
sumo --version
```

You want Python 3.13.x and native `Eclipse SUMO sumo 1.27.0`. Install the executable
separately from <https://eclipse.dev/sumo/> and put it on `PATH`. Only Stage 9 and
Stage 11 need it; everything else runs without it.

**2. Install dependencies**, once per component:

```bash
python -m pip install -r 05_Pipeline/route51_gen/requirements.txt
```

```bash
python -m pip install -r 05_Pipeline/route51_service/requirements.txt
```

**3. Keep the two folders side by side.** `route51_service/service_config.yaml` points at
`../route51_gen/output`. If the folders are separated, every service stage fails on a
missing input.

**4. Decide where to start — this matters.**

The archive ships the frozen matrix, per-run summaries and the release tables, but not
the raw SUMO outputs. Two sensible entry points:

| You want to | Start at | Notes |
|---|---|---|
| Reproduce the thesis numbers exactly | **Stage 8** (`run_layer3.py`) | Uses the shipped `fused_demand_matrix.csv` from `06_Results/tables`. Verified: reproduces the selected plan and the Layer-3 result. |
| Exercise the whole chain from raw generation | **Stage 1** | Expect small deviations from the shipped numbers — see the note below. |

> **Re-running Stages 1–7 will not reproduce the shipped demand matrix exactly.**
> The shipped `fused_demand_matrix.csv` was written on 22 July 2026; the Chapter 3
> results were computed from it on 28 July, and Layers 1–2 were not re-run in between
> while the fusion code continued to be refined. A clean re-run today produces a matrix
> that is very close but not identical: 6,820 OD cells against the shipped 6,974, and a
> total of 5,750 riders against 5,764 — a difference of **0.24%**, with the same 23
> boarding stops, the same hourly span, and a mean absolute difference of 0.37 riders per
> shared cell. The current code is in fact the tidier of the two: it pins the total to the
> 5,750 ATTO riders exactly, where the shipped file overshoots by 14.
>
> Layers 1 and 2 are themselves deterministic — two consecutive runs give byte-identical
> output. The discrepancy is version drift between the shipped artefact and the shipped
> code, not randomness. The confirmatory effect was not re-evaluated on this regenerated
> matrix, so its effect on the headline finding is unknown. To reproduce the reported
> numbers, start from Stage 8 with the frozen matrix.

---

## Part A — Layer 0: generate the data

Run these from `05_Pipeline/route51_gen/`.

```bash
cd 05_Pipeline/route51_gen
```

The generator builds one shared demand substrate — a synthetic population, its activity
schedules, its true trips — and then derives three *independently degradable* operator-style
feeds from it, plus the ground truth. Keeping one substrate behind three feeds is what
makes the fusion analysis meaningful: recovered OD can be scored against the truth.

### Stage 1 — corridor geometry

```bash
python run_geo_check.py
```

- **Needs:** `data/Busstations.csv` (23 stops, S01–S23), `config.yaml`
- **Writes:** `output/corridor_cells.geojson` — the H3 resolution-9 corridor grid, 262 cells
- **Time:** seconds
- Verifies that the route anchors and cordon gateways snap onto the grid.

### Stage 2 — population, trips, and the ground-truth oracle

```bash
python run_demand.py
```

- **Needs:** `config.yaml`, the stop catalogue
- **Writes:** `output/persons.parquet`, `output/trips.parquet`,
  `output/ground_truth_od_cells.csv`, `output/ground_truth_bus_od.csv`
- **Time:** a few minutes
- The two `ground_truth_*` files are the oracle. Layer 2 is scored against them; nothing
  downstream of Layer 2 is allowed to see them.

### Stage 3 — telecom and ATTO feeds

```bash
python run_feeds.py
```

- **Needs:** `persons.parquet`, `trips.parquet` from Stage 2
- **Writes:** `output/telecom_cdr_raw.csv` (62 MB) + `.parquet`, `output/atto_taps_raw.csv`
- **Time:** a few minutes
- Telecom: ~524,000 signalling events from ~11,000 subscribers of a single observed
  operator, coarse serving cell only, no coordinates. ATTO: 5,750 tap-in events, hashed
  card IDs, **no tap-out** — alighting-blindness is a modelled invariant of the real
  system, not an omission.

### Stage 4 — bus GPS feed

```bash
python run_gps.py
```

- **Needs:** `config.yaml` (timetable and route parameters)
- **Writes:** `output/bus_gps_raw.csv` + `.parquet`
- **Time:** under a minute
- 142 weekday trips over 12 buses, 30-second pings, one-way calibrated to 41.0 min.

> `fetch_boundaries.py` re-downloads the Tashkent district polygons from OpenStreetMap via
> Overpass. You do not need it — `data/tashkent_districts.geojson` is already in the
> archive. Run it only if you want to refresh the boundaries.

---

## Part B — Layers 1 to 5: the service

Run these from `05_Pipeline/route51_service/`.

```bash
cd ../route51_service
```

### Stage 5 — Layer 1: ingestion and governance

```bash
python run_layer1.py
```

- **Needs:** the three raw feeds from Stages 3–4, plus `corridor_cells.geojson` from Stage 1
- **Writes:** `output/{telecom,atto,gps}_indexed.parquet`,
  `output/{telecom,atto,gps}_aggregate.csv`, `output/governance_report.md`
- **Time:** ~25 seconds *(measured)* — dominated by parsing the 62 MB telecom CSV

Each feed passes a contract gate, then a privacy policy grounded in Uzbek law ZRU-547,
then H3 indexing and k-anonymity. The policy is applied **by privacy class**, because
over-suppressing non-personal data is as much an error as under-protecting personal data:

- **GPS** — non-personal vehicle telemetry. Identifier scan only; no pseudonymisation,
  no k-anonymity.
- **ATTO** — personal, pseudonymised. Hashed-ID format enforced, any tap-out field
  rejected, k-anonymity over distinct cards per (cell, time-bin).
- **Telecom** — personal, pseudonymised and localised. As ATTO, plus the in-country
  processing tag required by Art. 27-1.

The gate is fail-closed: a feed that breaches policy is quarantined, not ingested.
A healthy run reports all three feeds `ACCEPTED` with `governance: PASS`, telecom at
90.8% in-grid, and k-anonymity suppressing 721 telecom and 70 ATTO records.

### Stage 6 — Layer 2: data fusion

```bash
python run_layer2.py
```

- **Needs:** `*_indexed.parquet` from Stage 5, plus `ground_truth_bus_od.csv` for scoring
- **Writes:** `output/fused_demand_matrix.csv` — **the key artefact**, consumed by every
  layer above — and `output/layer2_validation_detail.csv`
- **Time:** ~2 seconds *(measured)*

Telecom gives a coarse cell-level OD; ATTO pins the boarding margins exactly but is blind
to alighting; trip chaining over card IDs supplies the alighting evidence. Bayesian
downscaling fuses the three into a stop-level OD by hour.

Fusion is analytical, not Bayesian: ATTO pins the boardings per stop and hour, and the
destinations are split in proportion to `α·(telecom prior share) + (chaining evidence) + ε`
with α = 5. No uncertainty is propagated.

Validation against the oracle for the **frozen pair shipped with the release**
(`06_Results/tables/fused_demand_matrix.csv` against `ground_truth_bus_od.csv`, checked by
`validate()` v2): boarding margin error **0.0** (ATTO pins it — the easy part), alighting
distribution cosine **0.882** (the hard part, and the actual job of the fusion), top-20 OD
recall **13 of 20**. The 0.906 and 0.911 figures quoted in earlier drafts come from other
runs — a clean-feed sweep and an earlier matrix — and are not the quality of this file.

### Stage 7 — Layer 2 robustness sweep *(optional)*

```bash
python run_sweep.py
```

- **Writes:** `output/layer2_sweep.csv`
- Re-runs the fusion under graded feed degradation to show how recovery accuracy decays.
  Nothing downstream depends on it; skip it if you only want the main result.

### Stage 8 — Layer 3: NSGA-II optimisation

```bash
python run_layer3.py --output-dir ../../06_Results/runs --run-id s12_layer3_central \
    --input-dir ../../06_Results/tables
```

- **Needs:** `fused_demand_matrix.csv` (shipped in `06_Results/tables`, or Stage 6)
- **Writes:** `layer3_result.json` (schema 2), `dispatch_baseline.csv`,
  `dispatch_candidate.csv`, `pareto_front.csv`, `run_meta.json`
- **Time:** ~2 minutes *(measured)* for three seeds

NSGA-II from pymoo, population 120 × 300 generations, seeds 42/43/44, SBX(0.9, η=15) and
PM(η=20). Decision variables are the headway and the 18 m share per period; objectives are
passenger waiting, the operating-cost index and crowding disutility in pax·min/day (ramp,
θ = 0.80, λ = 2.0 at full capacity), under the cost cap (×1.25), the cap on headway spread
across periods (δ = 0.20) and the normative fleet.

Every candidate is then rounded to whole trips by `dispatch_v1`, and the plan is selected
**after** that rounding: the lowest `waiting + crowd_min` among the candidates that are
still feasible, ties by lower cost, then by the identifier `<seed>:<index>`.

The 120 × 300 budget is a **correctness requirement, not tuning**. At the earlier 60 × 60
the final hypervolume varied 18.6 % across seeds and the recommended plan flipped from
better to worse than baseline depending on the draw; at 120 × 300 five seeds reach 95 % of
the final hypervolume by generation 65 and 99 % by 131, with a spread of 0.8 %.

A correct run selects plan `43:1` — `am_peak 10.8 min / 95 % 18 m`, `midday 13.0 / 0 %`,
`pm_peak 10.7 / 100 %`, `off_peak 13.0 / 0 %` — with dispatch metrics waiting
83,333 → 95,608, crowding 237,329 → 125,300, their sum −31.1 %, and the cost index
2,252.8 → 2,752.0 against a cap of 2,784.0.

### Stage 9 — Layer 4: SUMO smoke run

```bash
python run_replications.py --output-dir ../../06_Results/runs --run-id smoke_technical \
    --protocol ../../06_Results/releases/route51_v2_20260922/protocol.json \
    --input-dir ../../06_Results/runs/s12_layer3_central \
    --input fused_demand_matrix.csv=../../06_Results/tables/fused_demand_matrix.csv \
    --series technical --workers 6
```

- **Needs:** the Stage 8 run directory, `fused_demand_matrix.csv`, **SUMO** (`SUMO_HOME` set)
- **Writes:** `runs.csv`, `simulation_analysis.json`, the raw SUMO output per run
- **Time:** ~1 minute *(measured)* for the three technical seeds

The technical series is the smoke test: it builds the bidirectional network, generates
background traffic and runs the dispatched timetable on the protocol's technical seeds
(900001–900003 and 910001–910003), which are **not** part of the confirmatory series.
SUMO is invoked in batch through `subprocess`, not TraCI. Background traffic is strict: a
missing `randomTrips.py` fails the run instead of silently producing an empty-road result.

`run_layer4.py` is the earlier single-run script. It still reads `headway_schedule.csv` and
belongs to the historical protocol; it is kept for the legacy control only.

### Stage 10 — Layer 5: decision support

```bash
python run_layer5.py --output-dir ../../06_Results/runs --run-id s15_layer5 \
    --input layer3_result.json=../../06_Results/runs/s12_layer3_central/layer3_result.json \
    --input simulation_analysis.json=../../06_Results/runs/s14_confirmatory_n30/simulation_analysis.json \
    --input economics_result.json=../../06_Results/runs/s14_economics/economics_result.json
```

- **Needs:** the Layer-3, simulation and economics results
- **Writes:** `decision_record.json`, `DECISION_BRIEF.md`, the what-if dashboard
- **Time:** ~2 seconds *(measured)*

Consolidates the layers below into a decision record and a human-readable brief. It
recomputes nothing: every number it prints is read from a validated result, and a result
whose `input_match` or `plan_match` is not `exact` is reported as such rather than used as
if it belonged to this plan. The what-if dashboard is a standalone HTML file — open it in
a browser.

---

## Part C — the thesis experiment, sweeps, and figures

These produce the Chapter 3 evidence. All are run from `route51_service/` and all depend
on Stage 8 having been run.

### Stage 11 — the confirmatory series (this is the registered result)

```bash
python run_replications.py --output-dir ../../06_Results/runs --run-id s14_confirmatory_n30 \
    --protocol ../../06_Results/releases/route51_v2_20260922/protocol.json \
    --input-dir ../../06_Results/runs/s12_layer3_central \
    --input fused_demand_matrix.csv=../../06_Results/tables/fused_demand_matrix.csv \
    --series confirmatory --workers 6
python verify_from_raw.py --output-dir ../../06_Results/runs --run-id s14_verify_from_raw \
    --input-dir ../../06_Results/runs/s14_confirmatory_n30
```

- **Needs:** the Stage 8 run directory, the registered protocol, **SUMO**
- **Writes:** `runs.csv` (one row per run), `simulation_analysis.json`, raw SUMO output
- **Time:** ~8.5 minutes *(measured)* with six workers; ~50 s per run

30 runs per condition — baseline and plan — under protocol `route51_v2` v1.0: simulation
06:30–11:30, departure cohort 07:00–10:00, background traffic every 4 s, seeds
100001–100030 and 200001–200030, one registered Welch test at α = 0.05 on the mean
full-trip bus duration of the cohort averaged over both directions. Mann–Whitney U and
Cohen's d are computed but reported as exploratory; no family-wise correction applies.

A correct run reports 38.357 → 38.206 min, Δ = +0.151 min (95 % CI [0.055; 0.247],
t(56.6) = 3.14, p = 0.0027), 60 of 60 runs valid and no teleports. `verify_from_raw.py`
recomputes those statistics from the raw SUMO output as an independent check.

The boarding-speed-up sensitivity re-runs the plan arm at the other protocol values:

```bash
python run_replications.py --output-dir ../../06_Results/runs --run-id s14_sensitivity_speedup0 \
    --protocol ../../06_Results/releases/route51_v2_20260922/protocol.json \
    --input-dir ../../06_Results/runs/s12_layer3_central \
    --input fused_demand_matrix.csv=../../06_Results/tables/fused_demand_matrix.csv \
    --series sensitivity --speedup 0.0 --workers 6
```

At 0 % the plan takes 41.33 min and at 15 % 39.76 min — both slower than the baseline, so
the registered reduction depends entirely on the 30 % assumption.

### Stage 12 — figures, statistics, and sensitivity analyses

Order among these does not matter, except that the figure scripts need the runs they read
and `build_table31.py` needs Stages 8 and 11. Each takes the same `runctx` flags; the
run ids below are the ones in the release.

| Command (from `route51_service/`) | Run id | Produces |
|---|---|---|
| `run_convergence.py --input-dir ../../06_Results/tables` | `s13_convergence` | convergence tables and figure |
| `make_fig_convergence.py --input nsga2_convergence_seeds.csv=<s13_convergence>/nsga2_convergence_seeds.csv` | `s20_fig_convergence` | Figure 1, redrawn without re-running the optimiser |
| `make_pareto_fig.py --input-dir <s12_layer3_central>` | `s15_fig_pareto` | Figure 2 |
| `make_fig_runs.py --input runs_confirmatory.csv=… --input runs_speedup0.csv=… --input runs_speedup15.csv=…` | `s15_fig_runs` | Figure 3 |
| `run_equity_curve.py --input-dir ../../06_Results/tables` | `s13_equity_curve` | Figure 4, Table 4 |
| `run_tco_sweep.py --input-dir ../../06_Results/tables` | `s13_tco_sweep` | Figure 5 |
| `run_quality_curve.py` | `s13_quality_curve` | Figure 6 |
| `run_kanon_sweep.py --input-dir ../../06_Results/tables` | `s13_kanon_sweep` | Figure 8 |
| `run_operator_sweep.py` | historical | operator identity and share |
| `run_sensitivity.py --input-dir ../../06_Results/tables` | `s13_sensitivity_layer3` | crowding model, cost ratio, demand scale |
| `run_controls.py --input-dir ../../06_Results/tables` | `s13_controls` and later `s23_controls_feasible` | Original uncapped comparison and an added, post-hoc feasible simple rule; the latter is exploratory and does not alter the registered test |
| `run_economics.py --input-dir <s12_layer3_central> --input simulation_analysis.json=… --protocol ../../06_Results/releases/route51_v2_20260922/protocol.json` | `s14_economics` | §3.5 economics by scope; the protocol is mandatory when a simulation is supplied |
| `build_table31.py --input layer3_result.json=… --input simulation_analysis.json=… --input coverage_h2.csv=…` | `s14_table31` | Table 3.1 |
| `make_annex_diagrams.py --output-dir ../../06_Results/runs/s20_annex_diagrams` | `s20_annex_diagrams` | Annex figures B.1 and C.1 |
| `run_coverage.py` | historical | H2 coverage ratio (needs the WorldPop raster) |

The convergence run is the slowest here — it repeats the full NSGA-II budget across five
seeds. `make_fig31.py` and `run_convergence_configs.py` belong to the historical protocol.

### Extras

```bash
python test_governance.py          # 14 checks of the Layer-1 privacy policy
python -B -m pytest tests ../../10_Prototype/tests   # service and prototype tests
```

`api.py` is a FastAPI stub that puts a thin HTTP face on the Layer-1 gateway. It is not
needed to reproduce anything; serve it with `uvicorn api:app --reload` if you want to
poke at the ingestion boundary over HTTP.

---

## Dependency graph

```
Busstations.csv ─┬─> run_geo_check ─> corridor_cells.geojson ──────────────┐
                 └─> run_demand ─┬─> persons/trips ─> run_feeds ─> telecom, ATTO ─┐
                                 └─> ground_truth_bus_od  (ORACLE) ──────────┐    │
                    run_gps ─> bus_gps ─────────────────────────────────────┼────┤
                                                                            │    ▼
                          (everything above lives in route51_gen)           │  run_layer1 ─> *_indexed
                                                                            │       │
                                                                            ▼       ▼
                                                       ORACLE ──────────> run_layer2 ─> fused_demand_matrix
                                                                                    │
                                                                                    ▼
                                                                             run_layer3 ─> layer3_result.json
                                                                                              + dispatch_*.csv
                                                                                    │
                                             ┌──────────────────────────────────────┼───────────────┐
                                             ▼                    ▼                 ▼               ▼
                                   run_replications (SUMO) ─> run_economics ─> run_layer5     the sweeps
                                             │                                      │
                                             └────────── feedback to L3 ────────────┘
```

The rule: the whole generator first, then the service in order.

---

## Configuration

Two files hold everything; no constant is hard-coded in the run scripts.

- `route51_gen/config.yaml` — population, activity, and feed-degradation parameters. Every
  non-measured value is tagged `PLACEHOLDER` and the loader prints a warning listing them.
  Provenance tags `[D1] [D2] [D10]` point into `4_Source_Data/`.
- `route51_service/service_config.yaml` — baseline headways, NSGA-II budget, governance
  thresholds, simulation parameters.

Settings worth knowing before you change anything:

| Setting | Value | Why it is what it is |
|---|---|---|
| `operations.baseline_headways` | am 8, midday 15, pm 8, off-peak 20 | Recorded in the project as a March 2024 GTFS snapshot for route 51, but the feed is not in the package, so this is a **scenario input**, not a verified fact. The peaks were once set to 9, which made the baseline a minute slacker and inflated every gain. The 6 minutes quoted in early drafts was route 131's figure and is infeasible for Line 51 anyway — a 90-minute cycle over 12 buses floors the headway at 7.5 min. |
| `operations.nsga2` | pop 120, gen 300, seed 42 | Convergence requirement — see Stage 8. The release runs seeds 42/43/44. |
| `crowding` | ramp, θ = 0.80, λ = 2.0, linear above φ = 1 | The crowding disutility of the release, in pax·min/day. A **scenario assumption**: the sweep in `run_sensitivity.py` varies θ, λ and the model form. The legacy `operations.crowd_penalty: 10` is used only by the historical control. |
| `operations.cost_per_veh_km_uzs_12m` | `null` | No sourced money rate per equivalent km. `null` means unknown, not zero: the combined economic balance is not computed while it is unset. |
| `operations.demand_scale` | 3.0 | A **labelled loading scenario**, not a measurement. The placeholder mode share is unrealistically low; the multiplier is what makes crowding bind. Results depend on it. |
| `governance.k_anonymity` | 10 | Suppression threshold for personal aggregates (Art. 16). Stage 12's `run_kanon_sweep.py` sweeps k ∈ {5, 10, 20, 50}. |
| `governance.suppress_downstream` | `false` | `false` reproduces the pipeline as run for Chapter 3, where suppression binds on the published aggregate. The k-sweep sets it `true`, which withholds suppressed cells from Layer 2 as well — the setting under which the privacy guarantee actually binds on the optimisation path. |
| `simulation.replications.design` | `independent` | The thesis runs an independent-samples test. `paired` (common random numbers) is available and is statistically stronger, but is not what Chapter 3 reports. |

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `FileNotFoundError` on `persons.parquet` | Stage 2 not run. |
| `FileNotFoundError` on `*_indexed.parquet` | Stage 5 not run. |
| `FileNotFoundError` on `fused_demand_matrix.csv` | Stage 6 not run. |
| `FileNotFoundError` on `layer3_result.json` | Stage 8 not run, or `--input-dir` points elsewhere. |
| `layer3_result.json is not schema 2` | The run directory holds a historical result; re-run Stage 8. |
| `SUMO_HOME/tools not found` | Background traffic is required by the protocol; the run fails on purpose rather than simulating an empty road. |
| `FileNotFoundError` on `ground_truth_bus_od.csv` | The two folders are not siblings, or Stage 2 was skipped. |
| `sumo: command not found` | The native SUMO executable is not on `PATH`; the Python package alone is insufficient. Only Stages 9 and 11 need it. |
| `ModuleNotFoundError` (`pymoo`, `rasterio`, …) | Dependencies not installed for that component. Install its `requirements.txt`. |
| `UnicodeEncodeError` on a Windows console | The scripts print `·` and `→`. Set `PYTHONIOENCODING=utf-8`, or run in Windows Terminal. |
| Numbers differ slightly from the thesis after a full re-run | Expected if you started at Stage 1 — see "Decide where to start" above. |
| A result looks like the thesis but the run failed | It cannot be: results are validated under one contract and an archived series is never substituted for a run that did not execute. Check `validation_issues` in the result file. |
| A SUMO run hangs | `simulation.replications.teleport_s` (300 s) exists to break rare gridlock. Check that `traffic_lanes` is 2 when background traffic is on. |
