# AI System Architecture for Urban Bus Service Optimisation — Thesis and Reproducibility Package

**Andrey Grigorev · ITPU · Master's thesis · release `route51_v2_20260922`**

> Paths below refer to the current `Final` project and the September 2026 archive.
> Current release files are in `06_Results/releases/route51_v2_20260922/`; raw runs
> remain in the local `06_Results/runs/` directory and are not in the archive.

An AI system architecture for urban bus service optimisation based on aggregated telecom
and ATTO fare-payment data, designed and evaluated by simulation on a Tashkent
corridor. The computational testbed is **city bus Route 51** — 23 stops, 12.8 km,
both directions in the simulation of the current release.

The deposit archive contains the thesis, pipeline, accepted result tables and
instructions for running the pipeline stage by stage.

---

## What is in here

| Folder | Contents |
|---|---|
| `01_Thesis/` | English and Russian thesis masters. The current English PDF has 84 pages. |
| `05_Pipeline/` | Layer-0 generator, Layers 1–5 service, run instructions and illustrative Layer-0 snapshots. |
| `06_Results/` | Frozen release, input matrix, tables, figures and active-release pointer. Raw SUMO output is not shipped. |
| `07_Source_Data/` | D1–D12 reference notes and the source checklist. |
| `10_Prototype/` | Three-step planning console and guides. |

| Document | Read it for |
|---|---|
| `README.md` (this file) | Orientation, prerequisites, the five-minute version. |
| **`RUNBOOK.md`** | **The stage-by-stage run instructions.** Every command, in order, with its inputs, its outputs, and its approximate runtime. This is the authoritative document for how to run the pipeline. |
| `RESULTS_MAP.md` | Which file backs which number, table, and figure in the thesis. |

`route51_gen/README.md` describes the generator; `route51_service/README.md` describes
the accepted service release. `RUNBOOK.md` gives the execution order.

---

## Prerequisites

1. **Python 3.13** (results were produced on 3.13.13).
2. **Eclipse SUMO 1.27** on `PATH`. SUMO is the Layer-4 microsimulation engine. It is
   a native executable — install it separately from <https://eclipse.dev/sumo/> and
   check it with:

   ```bash
   sumo --version
   ```

3. Python dependencies, installed per component:

   ```bash
   python -m pip install -r 05_Pipeline/route51_gen/requirements.txt
   ```

   ```bash
   python -m pip install -r 05_Pipeline/route51_service/requirements.txt
   ```

`route51_service/requirements.txt` pins the direct dependencies in the 23 September
2026 verification environment. `route51_service/environment-full.txt` captures that
environment's full `pip freeze`; run metadata records the versions used for each
original result. This later snapshot alone does not prove an exact rebuild of the
original environment.

There is no container. The frozen matrix, protocol, run metadata and package checks
define the verified reproduction scope; a complete Layer-4 re-run is separately logged.

**Directory layout matters.** `route51_gen` and `route51_service` must stay siblings.
The service reads the generator's outputs by relative path (`../route51_gen/output`,
configured in `service_config.yaml`). Move one without the other and the pipeline will
not find its inputs.

---

## The five-minute version

You can read the results without running code in
`06_Results/releases/route51_v2_20260922/`. See `RESULTS_MAP.md` for provenance.

To re-run the chain, follow the commands in `RUNBOOK.md` from the project root.
It identifies the required input and output of each stage. The
freshly regenerated Layer-2 matrix does not exactly match the frozen release matrix.

---

## The headline result

Thirty SUMO runs per condition, both directions, under a protocol fixed before the
experiment (`route51_v2` v1.0). The primary outcome is the mean full-trip bus duration of
the 07:00–10:00 departure cohort:

| | Baseline | Plan `43:1` |
|---|---|---|
| Mean full-trip duration | 38.357 min (SD 0.200) | 38.206 min (SD 0.171) |

Δ = **+0.151 min** — about nine seconds, 0.39 % — with a 95 % Welch interval of
[0.055; 0.247] and *p* = 0.0027, so there is evidence of a reduction **in this model**.
Cohen's *d* = 0.81 and Mann–Whitney *p* = 0.003 are exploratory. All 60 runs are valid with
no teleports.

In the Layer-3 model the same plan raises waiting from 83,333 to 95,608 pax·min/day
(+14.7 %) and cuts crowding disutility from 237,329 to 125,300 (−47.2 %); their sum falls
31.1 % and the operating-cost index rises 22.2 %, within the ×1.25 cap. The plan lengthens
peak headways (8 → 10.8 min in the morning, 8 → 10.7 in the evening) while shifting nearly
all peak departures to 18 m vehicles, and shortens the slack midday and off-peak headways
(15 and 20 → 13.0).

Read that result with four caveats, all of which the thesis states:

- **The data are synthetic.** This is a demonstration that the method works end to end,
  not an empirical finding about the real Route 51. Absolute values are parametric.
- **The simulated gain rests on one assumption.** It exists only because 18 m buses are
  assumed to board 30 % faster; at 15 % and 0 % the plan takes 39.76 and 41.33 min, both
  slower than the baseline. No threshold of practical significance was set.
- **The modelled passenger benefit is crowding relief, and it is a valuation.** Waiting
  gets worse; the morning-peak valuation is about 5.3 bn UZS/yr at a waiting weight of 1
  and 4.2 bn at 2, almost all of it crowding. The operator's money rate per kilometre is
  unknown, so the combined balance is **not computed**.
- **Only H1 is tested, and in a refined form.** H2 (coverage) is not testable at fixed
  route topology — every plan serves every stop. H3 (Bottleneck Index) needs per-segment
  occupancy tracking that Layer 4 does not instrument, and is deferred. A simple capacity
  rule matches the optimiser once the spread cap is lifted. An added post-hoc simple
  rule that obeys the central cap improves the model objective by 9.2 % against the
  optimiser's 31.1 %, but spends 2 688 rather than 2 752 cost-index units and is
  exploratory; it does not establish general optimiser superiority.

---

## Architecture

Six layers, of which L0–L5 are implemented here.

```
L0  DATA GENERATION      route51_gen      population -> trips -> 3 observed feeds + ground truth
     |                                    (telecom CDR, ATTO taps, bus GPS; oracle for validation)
     v
L1  INGESTION + GOVERNANCE  route51_service  contract gate, ZRU-547 privacy checks,
     |                                       H3 indexing, k-anonymity
     v
L2  DATA FUSION          route51_service  telecom + ATTO + trip chaining -> demand matrix,
     |                                    validated against the oracle
     v
L3  OPTIMISATION         route51_service  NSGA-II over headways and vehicle mix, then
     |                                    rounding to whole trips; objectives: waiting /
     |                                    cost index / crowding disutility (pax·min)
     v
L4  MICROSIMULATION      route51_service  SUMO: travel time, bunching, fleet feasibility
     |
     v
L5  DECISION SUPPORT     route51_service  decision record, brief, equity matrix, dashboard
```

The three feeds are degraded independently, which is what makes the data-fusion analysis
possible: recovered OD versus ground-truth OD gives error as a *function* of degradation
rather than a single accuracy number.

SUMO is the evaluation engine at Layer 4 only. It is never a data source.

---

## Known limitations

Carried over from the thesis and the component notes, because anyone running this should
know them before quoting a number:

- **Synthetic throughout.** Calibration against real ATTO / telecom / OD data is an
  institutional step, not a technical blocker.
- **`demand_scale: 3.0`** in `service_config.yaml` is a labelled loading *scenario*, not a
  measured quantity. Results depend on it. The underlying placeholder mode share (~3.6%)
  is unrealistically low, so the multiplier is what makes crowding bind.
- **Placeholder artefact:** demand concentrates hard on stop S14 — an IT/university
  cluster against uniform residential density. Real data would redistribute this.
- **SUMO optimism bias:** inter-stop speed is a placeholder 30 km/h and there are no
  traffic signals, so running times are a lower bound. Comparing two plans against each
  other is far more robust than either absolute number. Background car traffic *is*
  enabled in `run_replications.py`, which is where the headline result comes from.
- **District-resolution population.** OpenStreetMap maps no admin_level 9 or 10 inside
  Tashkent, so the 12 district polygons are the finest zones available. Mahalla
  boundaries would materially sharpen both the coverage figure and the Layer-2
  downscaling.
- **WorldPop within Tashkent.** The raster reproduces Uzbekistan's national population to
  within 1.5%, but its distribution *inside* the city contradicts the official district
  register badly. `run_coverage.py` therefore uses the raster only for within-district
  shape and the D10 register figures as district control totals — the dasymetric
  procedure D10 §5 prescribes.
- **Route 51 only.** Line 131 appears in earlier drafts but is not simulated; every
  result here is Line 51.
- **Morning only in simulation.** The confirmatory series covers the 07:00–10:00 cohort;
  queues and denied boarding are not modelled, and the fleet check is normative, so
  operational feasibility is reported as unknown.
- **Crowding is a scenario model,** a ramp reaching twice the in-vehicle time multiplier at
  full capacity. Most of the modelled benefit depends on it.

---

## What is deliberately not in this archive

| Not included | Why |
|---|---|
| Supervisor reviews and earlier draft history | They are process records, not inputs to the released calculations. English and Russian thesis masters are both included. |
| `translation_ru/` — the RU translation toolchain | The current Russian thesis master is included; the editing toolchain is not needed to inspect it. |
| `thesis_edit_scripts/` — the python-docx scripts that patched the document across review rounds | Document-production tooling, not part of the staged pipeline. They need the earlier document versions to mean anything. |
| The Russian per-layer design notes that sat in `route51_service/` | Replaced by `RUNBOOK.md`, which is current for version 10. |
| `output_headway9/` — the superseded 9-minute-baseline run | The March 2024 GTFS snapshot gives 8 minutes for the peak. Everything was recomputed; none of that run's numbers appear in the thesis. |
| Earlier review history and audits | Process artefacts; the execution plan, execution log and follow-up journal are included for traceability. |
