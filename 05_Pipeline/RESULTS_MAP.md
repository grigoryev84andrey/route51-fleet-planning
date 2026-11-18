# RESULTS MAP — which file backs which number

Release `route51_v2_20260922`. Every figure and table of Chapter 3 is written from an
artefact in `06_Results/runs/<run_id>/`; nothing is typed in by hand. The detailed
definition of each quantity — unit, scope, uncertainty, evidence kind and the manuscript
places that use it — is in `../NUMBERS_MAP.md`; this file is the short index from thesis
object to artefact.

Artefacts in `06_Results/tables` and `06_Results/figures` belong to the earlier protocol
(single direction, winsorised peak travel time, the weight-10 overcrowding proxy) and are
kept as history. They are named below only where they are still current.

---

## Headline numbers

| Quantity | Value | Run / file |
|---|---|---|
| Primary outcome, baseline | mean full-trip bus duration 38.357 min (SD 0.200) | `s14_confirmatory_n30/simulation_analysis.json`, `runs.csv` |
| Primary outcome, plan `43:1` | 38.206 min (SD 0.171) | same |
| Registered effect Δ = baseline − plan | +0.151 min (0.39 %); 95 % CI [0.055; 0.247]; t(56.6) = 3.14; p = 0.0027 → `improved` | same |
| Effect size, rank test (exploratory) | Cohen's d = 0.81; U = 652, p = 0.0029 | same, `secondary_exploratory` |
| Dependence on the boarding assumption | plan at 15 % speed-up 39.760 min, at 0 % 41.329 min — both slower than baseline | `s14_sensitivity_speedup15`, `s14_sensitivity_speedup0` |
| Completeness | 60 of 60 runs valid, 0 teleports | `s14_confirmatory_n30/runs.csv` |
| Recomputation from raw SUMO output | statistics reproduced | `s14_verify_from_raw` |
| Selected plan | `43:1` — 10.8 / 13.0 / 10.7 / 13.0 min; 18 m share 95 / 0 / 100 / 0 % | `s12_layer3_central/layer3_result.json`, `dispatch_candidate.csv` |
| Model, waiting | 83,333 → 95,608 pax·min/day (+14.7 %) | `s12_layer3_central/layer3_result.json` |
| Model, crowding disutility | 237,329 → 125,300 pax·min/day (−47.2 %) | same |
| Model, sum and cost index | 320,663 → 220,908 (−31.1 %); 2,252.8 → 2,752.0 (+22.2 %), cap 2,784.0 | same |
| Demand-matrix quality (frozen pair) | alighting cosine 0.882; top-20 OD recall 13 of 20 | `validate()` v2 on `06_Results/tables/fused_demand_matrix.csv` + `ground_truth_bus_od.csv` |
| Convergence | 95 % of final hypervolume by generation 65, 99 % by 131; seed spread 0.8 % | `s13_convergence/nsga2_convergence_summary.csv` |

The registered analysis is one Welch test at α = 0.05, so no family-wise correction is
applied; Mann–Whitney U and Cohen's d are exploratory. The earlier Holm–Bonferroni
description belongs to the historical protocol.

---

## Figures and tables

| Thesis object | Run | Artefact |
|---|---|---|
| Table 3.1 — baseline KPIs | `s14_table31` | `table3_1_baseline_kpis.csv` / `.md` |
| Figure 1 (§3.3.2) — NSGA-II convergence | `s20_fig_convergence` (redrawn from `s13_convergence`) | `fig3_2_nsga2_convergence.png`, `nsga2_convergence_seeds.csv` |
| Figure 2 (§3.3.2) — candidates and selected plan | `s15_fig_pareto` | `fig3_3_pareto_front.png`, `pareto_front.csv` |
| Figure 3 (§3.4) — full-trip duration per run | `s15_fig_runs` | `fig3_runs_duration.png`, `runs_*.csv` |
| Figure 4 + Table 4 (§3.6.1) — headway-spread cap | `s13_equity_curve` | `fig3_4_equity_frontier.png`, `equity_curve.csv` |
| Figure 5 (§3.6.2) — operating-cost cap | `s13_tco_sweep` | `fig3_5_tco_sensitivity.png`, `tco_sweep.csv` |
| Figure 6 (§3.6.3) — feed-quality degradation | `s13_quality_curve` | `fig3_6_data_quality.png`, `quality_curve.csv` |
| Figure 7 (§3.6.3) — operator identity and share | historical (Layer 2 unchanged) | `06_Results/tables/operator_sweep.csv`, `figures/fig3_8_operator_sensitivity.png` |
| Figure 8 (§3.6.4) — k-anonymity | `s13_kanon_sweep` | `fig3_7_kanon_sensitivity.png`, `kanon_sweep.csv` |
| §3.3.1 coverage (H2) | historical | `06_Results/tables/coverage_h2.csv`, `coverage_by_district.csv` |
| §3.5 economics | `s14_economics` | `economics_result.json`, `economics.csv` |
| §3.6 controls and Layer-3 sensitivity | `s13_controls`, `s13_sensitivity_layer3` | `controls.csv` / `.json`, `sensitivity_layer3.csv` |
| Decision package (§3.5.4) | `s15_layer5` | `decision_record.json`, `DECISION_BRIEF.md` |
| Annex B — architecture diagram | `s20_annex_diagrams` | `figB1_architecture.png` / `.svg` |
| Annex C — pipeline diagram | `s20_annex_diagrams` | `figC1_pipeline.png` / `.svg` |
| Governance evidence | `run_layer1.py` | `governance_report.md` |

---

## The economics (§3.5)

From `s14_economics/economics_result.json`. Scopes are reported separately and unknown
quantities are left unknown rather than set to zero.

**Value of time.** 27,930 UZS/hour. Tashkent city gross monthly wage 11,849,080 UZS
(official quarterly series 2.02.02.1200, National Statistics Committee, 2026 H1), less the
flat 12 % personal income tax → 10,427,190 UZS net, divided by 168 monthly hours →
62,067 UZS/hour, times 0.45 for the commuting share. Reported with a ±30 % sensitivity
band, which is not a confidence interval. The series is nominal accrued (hence the tax) and
excludes agriculture and small business, which biases it upward; the city figure is used
because the study area is Tashkent.

| Scope | Components | Monetised |
|---|---|---|
| Morning peak (model) | crowding +233,944 pax·h/yr; waiting −42,577 pax·h/yr | 5.34 bn UZS/yr at waiting weight 1; 4.16 bn at weight 2; ±30 % VoT → 3.74–6.95 bn |
| Morning peak, simulated in-vehicle time | +1,325 pax·h/yr (derived from the confirmatory series) | ≈ 37 mn UZS/yr |
| Whole day (model) | crowding +466,790; waiting −51,149 pax·h/yr | 11.6 bn UZS/yr at weight 1; 10.2 bn at weight 2 |
| Whole day with simulated in-vehicle time | — | `not_evaluated` (the simulation covers the morning cohort only) |
| Operator | +124,800 12 m-equivalent km/yr (all day); +12,800 (morning) | `operator_rate` is `null`, so `combined_uzs` is `null`; the model balance stays ≥ 0 only below 93,019 UZS per equivalent km |

Almost all of the passenger valuation is crowding relief, which rests on the scenario
crowding model (ramp, θ = 0.80, λ = 2.0), not on a measured saving.

---

## Hypotheses

| | Status | Evidence |
|---|---|---|
| **H1** — travel-time reduction | Tested in the refined form **H1_bus_v2** (mean full-trip bus duration of the 07:00–10:00 cohort, both directions). Evidence of a reduction in the model: +0.151 min, 95 % CI [0.055; 0.247], p = 0.0027. Small (≈ 9 s per trip) and conditional on the 30 % boarding speed-up; no practical-significance threshold was set. The passenger form is not tested. | `s14_confirmatory_n30`, `s14_sensitivity_speedup0/15` |
| **H2** — coverage | **Not testable as posed.** With route topology fixed and every stop served under every plan, the 500 m coverage ratio is identical under the baseline and under every candidate. Reported as a scoping result. | `06_Results/tables/coverage_h2.csv` |
| **H3** — Bottleneck Index | **Deferred.** Needs per-segment occupancy tracking that Layer 4 does not instrument. | — |

Coverage figures, for the record: 45.4 % within the corridor band, 5.9 % across the seven
districts the corridor reaches, from the WorldPop 100 m raster used for within-district
shape only, with the D10 district register as control totals.

Controls (`s13_controls`): a simple capacity rule reaches −65.4 % against −64.6 % for the
optimiser without the spread cap, so no advantage of the optimiser over that rule is shown;
rebuilding the matrix without the telecom prior changes the plan little (−26.5 % against
−24.1 %) and does not reproduce the frozen matrix, so that comparison is exploratory.

---

## Provenance notes

**Historical artefacts.** `06_Results/tables/replications_*.csv`, `n30_replications_*.csv`,
`sec34_stats.csv`, `headway_schedule.csv`, `layer3_comparison.csv`, `economics.csv` and
`nsga2_convergence*.csv` come from the earlier protocol. They are kept for comparison and
are explained in `../NUMBERS_MAP.md`; no current number depends on them.

**Legacy control, 22 September 2026.** `06_Results/runs/ctl_legacy_20260922_b6348aa4`
re-runs the archived code against the shipped matrix and reproduces the earlier artefacts
byte for byte, which is what lets the old and new numbers be compared at all.
`n30_replications_raw.csv` came from a still earlier code version.

**Reproducibility.** Optimisation and simulation reproduce from the shipped
`fused_demand_matrix.csv`; re-running Layers 1–2 regenerates a matrix 0.24 % different from
the shipped one because of library-version drift between 22 and 28 July 2026 (see
`RUNBOOK.md`, "Decide where to start"). Every run directory carries `run_meta.json` with
the script hash, argv, effective configuration and library versions.

**Baseline timetable.** The peak headway of 8 minutes is recorded in the project as a
March 2024 GTFS snapshot, but the feed is not in the package, so it enters as a scenario
input. The earlier 9-minute baseline run is excluded and no number from it survives.
