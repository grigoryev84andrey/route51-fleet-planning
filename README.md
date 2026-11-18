# route51-fleet-planning

Fleet Planning Console and the Route 51 pipeline from the master's thesis
*An AI System Architecture for Urban Bus Service Optimisation Based on Aggregated
Telecom and ATTO Fare-Payment Data: Design and Simulation-Based Evaluation on a
Tashkent Corridor* (Andrey Grigorev, ITPU, 2026).

The repository holds a working three-step planning console (FastAPI back end, static
front end) and the five-layer pipeline it drives: synthetic data generation, privacy
aggregation, data fusion, NSGA-II headway optimisation, SUMO microsimulation and
economic evaluation. The test case is city bus Route 51 in Tashkent: 23 stops,
12.8 km.

All passenger, fare-payment and telecom data in this repository are **synthetic**.
No operational ATTO or mobile-operator data are included.

## Layout

| Folder | Contents |
|---|---|
| `10_Prototype/` | Fleet Planning Console: back end, front end, user guides (EN, RU), tests |
| `05_Pipeline/route51_gen/` | Layer 0: synthetic demand, fare-tap, GPS and signalling generator |
| `05_Pipeline/route51_service/` | Layers 1-5: aggregation, fusion, optimisation, simulation, economics |
| `05_Pipeline/layer0_snapshots/` | Illustrative Layer-0 snapshots and their schema |
| `06_Results/releases/route51_v2_20260922/` | Accepted release: protocol, inputs, tables, figures, manifest |
| `06_Results/tables/` | Frozen fused demand matrix and the Layer-2 / coverage tables still in use |

Folder names keep the numbering of the thesis workspace because the code resolves
paths relative to it. References in the documents to `01_Thesis`, `02_Defense`,
`07_Source_Data`, `08_Frontend_Design`, `11_Architecture` and `06_Results/runs` point
to parts of that workspace that are not published here.

## Quick start

Requirements: Python 3.13. Eclipse SUMO 1.27.0 (with `SUMO_HOME` set) is needed only
to run new simulations; saved results open without it.

```bash
cd 10_Prototype
python -m pip install -r requirements.txt
./run.sh          # Windows: run.cmd
```

The console opens at <http://127.0.0.1:8051>. See
[10_Prototype/docs/USER_GUIDE_EN.md](10_Prototype/docs/USER_GUIDE_EN.md).

To run the pipeline stage by stage, follow
[05_Pipeline/RUNBOOK.md](05_Pipeline/RUNBOOK.md).

Tests:

```bash
python -B -m pytest 05_Pipeline/route51_service/tests 10_Prototype/tests
```

## Not included

| Left out | How to obtain it |
|---|---|
| Raw synthetic signalling (`telecom_cdr_raw.*`, `*_indexed.parquet`, about 75 MB) | Regenerate with `05_Pipeline/route51_gen/run_feeds.py` |
| WorldPop raster `uzb_ppp_2020_constrained.tif` (20 MB) | Download from WorldPop; used only by the coverage script |
| Raw SUMO output and earlier-protocol outputs | Regenerate with the RUNBOOK commands |

Re-running Layers 1-2 does not reproduce the frozen matrix exactly (0.24 % difference
in demand volume from library version drift; see the RUNBOOK). Start from the frozen
matrix in `06_Results/releases/route51_v2_20260922/inputs` to reproduce the release
numbers.

## Data attribution

- District boundaries: © OpenStreetMap contributors, available under the Open Database
  License (ODbL); fetched by `05_Pipeline/route51_gen/fetch_boundaries.py`.
- Barlow fonts: © The Barlow Project Authors, SIL Open Font License 1.1
  (`10_Prototype/frontend/fonts/OFL.txt`).

## Licence

Code and documentation: MIT, see [LICENSE](LICENSE). Third-party data and fonts keep
their own licences listed above.
