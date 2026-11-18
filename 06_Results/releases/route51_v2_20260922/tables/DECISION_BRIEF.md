# Route 51 — Planning brief (synthetic model)
*Layer 5 summary of layer-3 run `s12_layer3_central`. Demand scenario ×3.0; crowding ramp (θ = 0.8, λ(1) = 2.0). Results describe the model only; operational feasibility is unknown.*

## 1. Selected plan (protocol rule)
Selected for the chosen priorities by minimum waiting + crowding disutility among plans feasible after dispatch: **43:1**.

| Period | Baseline headway | Plan headway (effective) | 18 m trips / all, per direction |
|---|---|---|---|
| am_peak | 8.0 min | 10.59 min | 16 / 17 |
| midday | 15.0 min | 12.86 min | 0 / 28 |
| pm_peak | 8.0 min | 10.59 min | 17 / 17 |
| off_peak | 20.0 min | 12.41 min | 0 / 29 |

## 2. Model comparison on the executable timetable
| Measure | Baseline | Plan | Change |
|---|---|---|---|
| Waiting (pax·min/day) | 83 333 | 95 608 | +14.7% |
| Crowding disutility (pax·min/day) | 237 329 | 125 300 | -47.2% |
| Waiting + crowding (pax·min/day) | 320 662 | 220 908 | -31.1% |
| Operating-cost index | 2 252.8 | 2 752.0 | +22.2% |
| Maximum load / full capacity | 1.63 | 1.36 | -16.9% |
| Headway spread across periods | 0.961 | 0.195 | -79.7% |
| Vehicles needed (normative) | 12 | 10 | -16.7% |

Worsening and improvement are shown side by side; the plan trades longer waits for less crowding when waiting rises.

## 3. Simulation
- Status: **see analysis** (evidence: confirmatory, effect: improved).
- Mean full-trip bus duration, 07:00–10:00 cohort, both directions: baseline 38.36 min, candidate 38.21 min; Δ = baseline − candidate = +0.15 min (95% CI +0.05 … +0.25), n = 30 + 30.
- This is bus full-trip duration, not an individual passenger's in-vehicle time.

## 4. Economics
- See `economics_result.json`; each block states its scope and status.

## 5. Headway-spread options (exploratory)
*relaxed optimum per cap, seed 42 (exploratory; not re-checked on dispatch).* Baseline (relaxed) waiting + crowding: 335 936.

| Cap on headway spread | Waiting + crowding (relaxed) | vs baseline |
|---|---|---|
| 0.10 | 257 389 | -23.4% |
| 0.20 | 217 919 | -35.1% |
| 0.30 | 198 797 | -40.8% |
| none | 93 327 | -72.2% |

The cap limits variation of headways between periods; choosing it is a policy decision. It is not a measure of spatial equity.

## 6. Limitations
- Synthetic methods demonstration — not an empirical claim about real Line 51.
- Demand scenario ×3.0 is a labelled assumption.
- Crowding parameters (theta, lambda) are scenario assumptions, not local measurements.
- Every trip of a period is modelled as an average vehicle; queues and denied boarding are not modelled.
- Fleet check is normative (departures per cycle), not a vehicle-block schedule; operational feasibility is unknown.
- The headway-spread cap limits variation between periods; it is not a spatial-equity measure.
