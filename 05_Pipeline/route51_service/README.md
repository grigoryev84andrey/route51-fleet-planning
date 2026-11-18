# Route-51 Optimization Service

Consumes the Layer-0 generator's output and works up the six-layer architecture to a
route-change recommendation. **SUMO is the Layer-4 evaluation engine, not a data source.**
All six layers are implemented; everything below describes release
`route51_v2_20260922`.

Numbers in this file are the release values. The mapping "number → file → run" is
`06_Results/../NUMBERS_MAP.md`; earlier values (single direction, winsorised peak travel
time, the weight-10 overcrowding proxy) are historical and are kept only in that map and
in `06_Results/tables`.

## How a run is made reproducible

Every script starts through `runctx.start()`, which

- resolves inputs **before** creating the run directory, so a failed run leaves nothing;
- takes `--config`, `--output-dir`, `--run-id`, `--input-dir`, `--input NAME=PATH`,
  `--protocol`, `--legacy-output`;
- writes `run_meta.json` with the script SHA-256, the full argv, the effective
  configuration and its SHA-256, library versions and timings.

Results are written under one contract (`result_contracts.py`, `schema_version: 2`) and
validated strictly: `input_match`, `plan_match`, `evidence_kind`, `execution_state`,
`completion_status`, `protocol_status`, `effect_status`, `operational_feasibility`,
`validation_issues`. A result that was not produced by the run being reported is never
substituted by an archived one.

Run the tests with the interpreter you actually use:

```bash
python -B -m pytest tests ../../10_Prototype/tests
```

## Layer 1 — Ingestion and governance

| Element | Module | What it does |
|---|---|---|
| Secure API gateway | `layer1/gateway.py` + `api.py` | one ingestion boundary: auth stub → schema gate → governance → indexing; fail-closed (a breaching feed is quarantined) |
| Anonymisation check | `layer1/governance.py` | per-feed policy grounded in ZRU-547: forbidden fields, direct-identifier scan, pseudonym format, telecom localisation |
| H3 indexing | `layer1/indexing.py` | H3 res 9 + macro 7, time bins, in-grid flag, per-(cell, bin) aggregate, k-anonymity on distinct persons |
| Static reference | `layer1/reference.py` | stop catalogue, published H3 grid, fleet catalogue |
| Contracts | `layer1/contracts.py` | per-feed fields, roles and privacy class |

Rules by privacy class: GPS is non-personal (identifier scan only); ATTO is personal and
pseudonymised, with alighting blindness as an invariant; telecom is personal, pseudonymised
and localisation-bound — `processing_region = UZ` per Art. 27¹ of ZRU-547 **as amended by
ZRU-1125 of 26 March 2026** (lex.uz/docs/8104580). These are rule checks on synthetic
data, not proof that a deployed system complies with the law.

Evaluated configuration: `k_anonymity: 10`, `suppress_downstream: false` (the k-sweep
re-runs the chain with `true`). Outputs: `*_indexed.parquet`, `*_aggregate.csv`,
`governance_report.md`.

```bash
python run_layer1.py --output-dir DIR --run-id ID
python test_governance.py   # 14 governance checks (standalone script, not pytest)
```

## Layer 2 — Demand estimation (fusion)

| Element | Module | Method |
|---|---|---|
| Telecom OD | `layer2/telecom_od.py` | modal serving cell per (subscriber, hour) → cell-level OD |
| Alighting inference | `layer2/alight_inference.py` | ATTO trip-chaining: alight(k) ≈ board(k+1); the last trip closes on the first boarding |
| Analytical fusion | `layer2/downscaling.py` | ATTO boardings fix the origin totals; destinations split in proportion to `α·(telecom prior share) + (chaining evidence) + ε`, α = 5. Point estimate only — no uncertainty is propagated. The Bayesian form (Dirichlet prior, Gibbs sampler) is the design intention, not what runs |
| Validation | `layer2/validation.py` | fused matrix against the generator oracle; boarding margins separated from alighting |

**Frozen pair used by every result of the release** (`fused_demand_matrix.csv` +
`ground_truth_bus_od.csv`, 6,974 rows, 5,764 riders): boarding margins exact, alighting
cosine **0.882**, top-20 OD recall **13 of 20**. The older 0.906 and 0.911 figures belong
to earlier and clean-feed runs and are not the quality of this matrix.

Degradation stays graceful and source-attributable: degrading the fare feed lowers the
alighting cosine to 0.782 and biases boardings downwards; degrading telecom lowers it only
to 0.894 (`run_quality_curve.py`, `run_sweep.py`).

```bash
python run_layer2.py --output-dir DIR --run-id ID --input-dir ../route51_service/output
```

## Layer 3 — Optimisation

| Element | Module | Detail |
|---|---|---|
| Operations model | `layer3/operations.py` | 4 periods; OD → load profile → waiting, cost index, crowding disutility, headway spread |
| Crowding | `layer3/operations.py` (`ivt_multiplier_v1`) | ramp: `λ = 1 + (λ_full − 1)·max(0, (φ − θ)/(1 − θ))`, θ = 0.80, λ_full = 2.0, linear above φ = 1; `crowd_min = Σ (λ−1)·L·t` in pax·min/day |
| NSGA-II | `layer3/optimize.py`, `layer3/study.py` | per-period headway + 18 m share; pop 120, 300 generations, seeds 42/43/44 |
| Dispatch | `layer3/dispatch.py` (`dispatch_v1`) | whole-second departures, 18 m count `floor(f·N + 0.5)` spread by Bresenham, cohort window 07:00–10:00 |
| Resources, provenance | `layer3/resources.py`, `layer3/provenance.py` | normative fleet and vehicle-km; fingerprints of input, plan and model |
| Exploratory comparison | `layer3/evaluate.py` | demand-draw resampling; **not** evidence for H1 |

Constraints: cost index ≤ baseline × `tco_factor` (1.25); headway spread across periods
≤ δ = 0.20 (a temporal spread cap, not spatial equity); peak concurrent vehicles ≤ fleet
(a normative check, not a duty schedule).

Selection rule: among candidates that remain feasible **after** rounding to whole trips,
the lowest `waiting + crowd_min`; ties by lower cost, then by the stable identifier
`<seed>:<index>`.

**Result of the central run** (`06_Results/runs/s12_layer3_central`), executable plan,
×3 demand scenario, pax·min/day:

| | waiting | crowd_min | sum | cost index |
|---|---|---|---|---|
| Baseline (8/15/8/20, all 12 m) | 83,333 | 237,329 | 320,663 | 2,252.8 |
| Plan `43:1` (10.8/13.0/10.7/13.0 min; 18 m 95/0/100/0 %) | 95,608 (+14.7 %) | 125,300 (−47.2 %) | 220,908 (−31.1 %) | 2,752.0 (+22.2 %), cap 2,784.0 |

Outputs: `layer3_result.json`, `dispatch_baseline.csv`, `dispatch_candidate.csv`,
`pareto_front.csv`.

```bash
python run_layer3.py --output-dir ../../06_Results/runs --run-id s12_layer3_central \
    --input-dir ../../06_Results/tables
```

## Layer 4 — SUMO microsimulation

| Element | Module | Detail |
|---|---|---|
| Network | `layer4/network.py` (`build_network_bidir`) | both directions of the 23-stop corridor with real inter-stop lengths → `netconvert` |
| Background traffic | `layer4/background.py` | `randomTrips.py` + `duarouter`, strict mode: a missing SUMO tool fails the run instead of silently dropping traffic |
| Scenario | `layer4/scenario.py` | buses follow the dispatched timetable; dwell ∝ boardings + alightings; 18 m board 30 % faster (scenario assumption) |
| Experiment, analysis | `layer4/experiment.py`, `layer4/analysis.py` | series runner and the registered statistics |

Registered protocol `route51_v2` v1.0 (`06_Results/releases/route51_v2_20260922/protocol.json`):
simulation 06:30–11:30, departure cohort 07:00–10:00, background traffic every 4 s,
seeds 100001–100030 (baseline) and 200001–200030 (plan), primary outcome = mean full-trip
bus duration averaged over both directions, one Welch test at α = 0.05.

**Confirmatory series** (`s14_confirmatory_n30`): 38.357 → 38.206 min, Δ = +0.151 min
(95 % CI [0.055; 0.247], t(56.6) = 3.14, p = 0.0027), Cohen's d = 0.81 (exploratory),
60 of 60 runs valid, no teleports. The reduction depends on the assumed boarding speed-up:
at 15 % the plan takes 39.76 min and at 0 % 41.33 min, both slower than the baseline.

```bash
python run_replications.py --output-dir ../../06_Results/runs --run-id s14_confirmatory_n30 \
    --protocol ../../06_Results/releases/route51_v2_20260922/protocol.json \
    --input-dir ../../06_Results/runs/s12_layer3_central \
    --input fused_demand_matrix.csv=../../06_Results/tables/fused_demand_matrix.csv \
    --series confirmatory --workers 6
python verify_from_raw.py --output-dir ../../06_Results/runs --run-id s14_verify_from_raw \
    --input-dir ../../06_Results/runs/s14_confirmatory_n30
```

`--series technical` runs the three technical seeds, `--series sensitivity --speedup 0.0|0.15`
the boarding-speed-up sensitivity. `verify_from_raw.py` recomputes the reported statistics
from the raw SUMO output. `run_layer4.py` is the earlier single-run script, kept for the
historical control only.

## Layer 5 — Decision support and economics

| Element | Module | Detail |
|---|---|---|
| Economics | `layer5/economics.py`, `run_economics.py` | VoT 27,930 UZS/h (0.45 × Tashkent net wage, first half of 2026), 250 days, by scope |
| Decision record and brief | `layer5/decision.py`, `layer5/brief.py` | `decision_record.json`, `DECISION_BRIEF.md` — no new computation |
| Dashboard, reports | `layer5/dashboard.py`, `layer5/reports.py` | self-contained what-if HTML, report files |

Scopes are kept apart and unknowns are not filled with zeros: morning peak 5.34 bn UZS/yr
(4.16 bn at a waiting weight of 2), whole-day model 11.6 bn, whole day with simulated
in-vehicle time `not_evaluated`. Simulated in-vehicle time adds about 37 mn. The operator
runs +124,800 12 m-equivalent km a year; `cost_per_veh_km_uzs_12m` is `null`, so
`operator_rate` and `combined_uzs` stay `null` and the reported threshold is 93,019 UZS per
equivalent km.

```bash
python run_economics.py --output-dir ../../06_Results/runs --run-id s14_economics \
    --input-dir ../../06_Results/runs/s12_layer3_central \
    --input fused_demand_matrix.csv=../../06_Results/tables/fused_demand_matrix.csv \
    --input simulation_analysis.json=../../06_Results/runs/s14_confirmatory_n30/simulation_analysis.json
python run_layer5.py --output-dir ../../06_Results/runs --run-id s15_layer5 \
    --input layer3_result.json=../../06_Results/runs/s12_layer3_central/layer3_result.json \
    --input simulation_analysis.json=../../06_Results/runs/s14_confirmatory_n30/simulation_analysis.json \
    --input economics_result.json=../../06_Results/runs/s14_economics/economics_result.json
```

## Sweeps, controls and figures

| Script | Run | What it shows |
|---|---|---|
| `run_convergence.py` | `s13_convergence` | 95 % of final hypervolume by generation 65, 99 % by 131, spread 0.8 % across 5 seeds |
| `run_tco_sweep.py` | `s13_tco_sweep` | cost cap ±10 %: 0.0 / −31.1 / −37.8 % |
| `run_equity_curve.py` | `s13_equity_curve` | δ = 0.10/0.20/0.30/unbounded: −13.3 / −31.1 / −30.5 / −64.6 %, worst headway up to 29.9 min |
| `run_quality_curve.py`, `run_sweep.py`, `run_operator_sweep.py` | `s13_quality_curve` | feed quality and operator share |
| `run_kanon_sweep.py` | `s13_kanon_sweep` | k = 5/10/20/50: 22–79 % of cells suppressed, change stays −21…−26 % |
| `run_sensitivity.py` | `s13_sensitivity_layer3` | crowding model, cost ratio r, demand scale |
| `run_controls.py` | `s13_controls`, `s23_controls_feasible` | Original uncapped comparison: simple capacity rule −65.4 % against −64.6 % for the uncapped optimiser. The later feasible equal-frequency rule achieves −9.2 % within δ=0.20 and the same budget cap; the central optimiser achieves −31.1 % with slightly higher actual cost. This post-hoc comparison does not establish general optimiser superiority. The telecom prior contributes little in its separate paired rebuild. |
| `make_fig_convergence.py`, `make_pareto_fig.py`, `make_fig_runs.py`, `make_annex_diagrams.py`, `build_table31.py` | `s20_fig_convergence`, `s15_fig_pareto`, `s15_fig_runs`, `s20_annex_diagrams`, `s14_table31` | release figures and Table 3.1 |

## Known limits of this implementation

- Everything runs on synthetic data; the numbers describe the model, not operational Line 51.
- The morning cohort only is simulated; queues and denied boarding are not modelled.
- The fleet check is normative — vehicle blocks and driver duties are not scheduled, so
  operational feasibility is `unknown`.
- Differential privacy (ε-DP) is specified in the architecture and not implemented.
- The Layer-1 boundary is respected logically (pseudonyms, country tag, forbidden fields),
  not physically: trip inference from telecom events runs in Layer 2.
