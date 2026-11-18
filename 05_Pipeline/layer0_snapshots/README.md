# Early prototype snapshot — NOT the dataset behind Chapter 3

This folder holds an early prototype of the Layer-0 data contract. It did **not**
produce any result in the thesis. Everything in Chapter 3 comes from
`../3_Pipeline_and_Results/route51_gen/`, which is a separate and later model.

The two differ substantially, and the difference is deliberate rather than an error:

| | this prototype | pipeline behind Chapter 3 |
|---|---|---|
| Line 51 stops | 12 (`R51_01`…`R51_12`) | 23 (`S01`…`S23`) |
| Fleet | 5 vehicles, 8 m and 12 m | 12 vehicles, 12 m and 18 m |
| Scale | 2,305 boardings | 5,750 bus riders from 80,000 agents |

The stop lists share no identifiers or names: they are two different representations
of the route, not two levels of detail of one.

**One thing does carry over into the thesis**: the baseline headways in
`gtfs_baseline_headways.csv` (8 min at both peaks, 15 midday, 20 off-peak), which
§3.1.2 uses as the March 2024 GTFS baseline. The stop list and the 12.8 km length
quoted there come from the operator's route definition, not from this folder — §3.1.2
says so explicitly.

Route 131 has been removed from every file here; the study is Line 51 only.
