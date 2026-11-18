# Release route51_v2_20260922

Frozen set of results for the thesis, the defence and the prototype. Every number in
the current documents comes from here or from the run directory a file names.
The s23 feasible simple-rule control is a later exploratory supplement; it does not
change the frozen protocol or the registered simulation result.

| File | What it is |
|---|---|
| `protocol.json` | Registered protocol `route51_v2` v1.0, frozen before the confirmatory series |
| `freeze_S14.json` | State fixed immediately before that series |
| `manifest.json` | Contents with SHA-256, the runs behind them, the parameter table, the master files |
| `quality_metrics.json` | `validate()` recomputed on the frozen matrix and oracle pair |
| `inputs/` | The frozen demand matrix, the oracle, and both configurations |
| `tables/` | Result tables of the release, named by their run where two runs share a file name |
| `figures/` | Figures used in the thesis and the defence |

Quality of the frozen pair: alighting cosine 0.881564629531548, top-20 OD recall 0.65.

Raw SUMO output is not copied here; it stays in the run directories listed under
`raw_outputs` in the manifest. Earlier results (`06_Results/tables`, `figures`,
`route51_service/output`, `10_Prototype/exports`) are historical: they were produced
under a different protocol and are explained in `NUMBERS_MAP.md`.

How to reproduce: `05_Pipeline/RUNBOOK.md`, stages 8 to 12. Each run directory carries
`run_meta.json` with the script hash, the full argv, the effective configuration and its
hash, and the library versions.

This directory is not published, carries no DOI, and is not synchronised anywhere.
