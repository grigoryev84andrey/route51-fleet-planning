# Fleet Planning Console — user guide

**Route 51 · synthetic model · release `route51_v2_20260922`**

The console drives the thesis pipeline: it estimates demand, runs the optimiser, compares
the resulting plans against the current timetable, opens a matching registered result or
runs a new simulation when SUMO is installed, and writes a report. It computes no science of its own; every number
comes from `../05_Pipeline`, and every figure says where it came from.

Everything here is synthetic. The numbers describe the model, not operational Line 51.

---

## 1. Start it

```bash
python -m pip install -r requirements.txt
```

Then `run.cmd` (Windows) or `./run.sh`, and open <http://127.0.0.1:8051>. The console always opens on **Conditions**, including when a previous session has calculated plans. Its results remain available through the next steps.

**Eclipse SUMO 1.27** with `SUMO_HOME` set (and `tools/randomTrips.py` present) is needed
for a new simulation. A matching result from the accepted release can be opened without
SUMO; the result of another plan is never substituted.

The console reads two sibling folders and writes to neither:

- `../05_Pipeline/` — the generator and the service layers, imported as Python modules;
- `../06_Results/` — the frozen demand matrix, the published sweeps, and the accepted
  release if `active_release.json` points at one.

Reports are written to `exports/`.

---

## 2. The path

Three steps, one question each:

| Step | Question | What runs |
|---|---|---|
| **1 · Conditions** | Which data, and which limits? | Layers 0–2: generation, ingestion at k = 10, fusion, validation against the oracle |
| **2 · Compare plans** | Which plan? | Layer 3: NSGA-II (Non-dominated Sorting Genetic Algorithm II) on three protocol seeds, then rounding to whole trips |
| **3 · Validate & export** | Do I accept it? | Layer 4 (optional, registered protocol) and Layer 5: economics and the report |

You can move back at any time. Reloading the page returns to **Conditions**; a running stage
reattaches to its progress bar instead of restarting.

---

## 3. Step 1 — Conditions

### Start from

| Choice | What happens | Takes |
|---|---|---|
| **Open thesis example** | Loads the frozen synthetic matrix the thesis was written from and validates it against the generator's oracle. The plans are recalculated now; the *earlier published* results attached to that matrix are labelled a historical run with validation issues. | instant |
| **Create a scenario** | The Layer-0 generator writes fresh fare-tap and signalling feeds, Layer 1 ingests and suppresses them at k = 10, Layer 2 fuses and validates them. | ≈ 10 s |

Under **Create a scenario** you pick one of five conditions — *As generated*, *Degraded
telecom* (25 % of signalling missing, 20 % oscillation), *Degraded fare feed* (28 % of taps
not recorded), *Peak demand +50 %*, *Both feeds degraded* — and, under **Advanced
settings**, the random seed.

**Random seed for generated data** sets the initial state of the random-number
generator when you **Create a scenario**. The same seed and settings reproduce
the same synthetic fare-tap and signalling records; changing it changes the
particular losses and noise, which can then change estimated demand and plans.
**42** is the fixed default in the configuration for reproducibility; the
number has no special statistical advantage. **Open thesis example** loads
an already saved matrix, so changing this field does not regenerate it.
This data-generation seed is separate from the optimiser's fixed **42, 43,
44** seeds.

### The three limits

- **Demand scale (assumption)** — ×1, ×2 or ×3. The generator's placeholder mode share is
  unrealistically low, so ×3 is the thesis scenario, the setting under which crowding binds.
  It is a labelled assumption, not a measurement.
- **Maximum operating-cost increase** — +12.5 %, +25 % or +37.5 % over the
  current timetable's index **before trips are rounded**. +25 % is the thesis setting.
- **Maximum headway spread across periods** — 10 %, 20 %, 30 % or no limit, computed as
  (longest − shortest) / mean. It caps how uneven service may be **across periods of the
  day**; it is not a measure of spatial equity.

**Advanced settings** also states what cannot be changed here: NSGA-II with a population of
120 on the protocol seeds 42, 43 and 44, and the crowding model
`λ = 1 + (λ_full − 1)·max(0, (φ − θ)/(1 − θ))`, `crowd_min = Σ (λ − 1)·L·t`.

**NSGA-II** searches for
different timetables with lower waiting, a lower cost index and less crowding
disutility. A population of 120 means 120 timetable candidates in each
generation; it runs 300 generations for each of three seeds. Different
seeds help explore different parts of the solution set. These settings
govern the search; they do not predict passenger numbers or create three
different source datasets.

The crowding model translates a crowded ride into **additional equivalent
minutes**. Here `φ` is demand on a segment divided by the capacity of trips
serving that segment in the period; `θ = 0.80` is the comfort threshold;
`λ_full = 2` is the multiplier at full capacity; `L` is the passengers on the
segment during the period; and `t` is the segment's running time in minutes.
At or below 80 % capacity `λ = 1` and there is no added penalty; at 90 %
`λ = 1.5`; at 100 % `λ = 2`. Summing `(λ − 1)·L·t` over segments and periods
gives **crowd_min**, in passenger-minutes per day. It measures modelled
discomfort, not extra clock time spent travelling. The formula extends
linearly above 100 % capacity; queues and denied boarding are not modelled.

### The demand estimate

The card reports, for exactly the matrix on screen:

- **Alighting distribution similarity** shows how closely the model
  reproduces the distribution of alighting stops for each boarding stop and
  hour. Each distribution is compared with the generator's synthetic ground
  truth; the results are then averaged with weights based on boardings.
  A value nearer 1 on the 0–1 scale means a more similar distribution.
  The frozen matrix scores **0.882**. This does not mean that 88.2 % of
  passengers were assigned their exact destination, or establish accuracy
  on an operational route.
- **Largest 20 OD flows recovered**: OD means *origin–destination*, a
  boarding/alighting stop pair; here each flow also has an hour. The
  metric compares the 20 largest true flows with the 20 largest recovered
  flows and counts matching stop-pair/hour keys. **13 of 20**, or 65 %,
  match for the frozen matrix. It does not measure the accuracy of their
  passenger counts or of all smaller flows.
- **Modelled daily boardings** sums boardings in the modelled day **after**
  **Demand scale** is applied. The frozen matrix sums to 5,764; the
  scenario multiplier ×3 gives **17,292**. This is not an observed daily
  boarding count; changing the scale changes it proportionally.
- **Input records** counts the separate fare taps and signalling events
  generated for a new scenario before fusion. It is an input-record count,
  not unique passengers or trips. The frozen matrix says "not recorded"
  because its source-feed counts were not retained, not because there
  were zero records.

On connected or uploaded data these similarity figures would not exist at all: there is no
true matrix to compare against. Operational data import is not available in this version.

---

## 4. Step 2 — Compare plans

The table puts the current timetable beside up to three alternatives, on the same measures:

| Measure | Meaning |
|---|---|
| Waiting | passenger waiting, pax·min/day |
| Crowding disutility | `Σ (λ − 1)·L·t`, pax·min/day — a modelled disutility, not a passenger count |
| Waiting + crowding disutility | the quantity the selection rule minimises |
| Operating-cost index | relative vehicle-km; the cost limit applies to it |
| Longest headway | the worst-served period |
| Vehicles needed (normative) | a normative count, not a duty schedule |
| Highest load / full capacity | the peak load factor |
| Within the limits after rounding to whole trips | feasibility **after** the plan becomes an executable timetable |

**Operating-cost index** sums `one-way route length × relative vehicle
factor` for every trip in both directions during the modelled day. The
factor is **1** for a 12 m bus and **1.5** for an 18 m bus; the one-way
length is **12.8 km**. The current timetable has 176 trips, all by 12 m
buses, so `176 × 12.8 × 1 = 2,252.8` relative vehicle-km. The displayed
plan has 116 trips by 12 m buses and 66 by 18 m buses, giving
`(116 + 66 × 1.5) × 12.8 = 2,752.0`, a 22.2 % increase. This is a
**resource index**, not a verified monetary cost: the 1.5 factor is a
configuration assumption and no monetary vehicle-km rate is available.
The +25 % limit applies to the **unrounded** current-timetable index
of 2,227.2, so the cap is `2,227.2 × 1.25 = 2,784.0`, not 2,816.0.
The comparison table reports both timetables **after** rounding to trips.
Thus the displayed 22.2 % change and the configured +25 % limit have
slightly different baseline values. All figures depend on the selected
settings and plan.

**Within the limits after rounding to whole trips** checks the timetable
with whole trips and vehicles, rather than the optimiser's preliminary
continuous values. A `yes` requires all three conditions at once: the
cost index is no higher than its cap, the spread of effective headways
across periods is no higher than its cap, and normative vehicles needed
are no more than the available **12**. In the standard example the
current timetable reads `no (current timetable)` because its headway
spread is **96.1 %**, above the selected **20 %**, although its cost
and normative vehicle count pass. The plan reads `yes`: spread **19.5 %**,
index **2,752.0** below **2,784.0**, and **10** normative vehicles.
`No` does not mean that the existing timetable cannot run; it fails a
**newly selected** comparison condition. `Yes` does not verify that buses
and drivers are available or that their duty schedules can be built;
the fleet check is normative only.

The named alternatives are **Recommended for the selected priorities** (the lowest
waiting + crowding disutility among plans still within the limits after rounding),
**Least waiting** and **Least crowding**. When one plan meets several selection choices,
its labels are combined in a single column. The radio button in the column heading
selects the plan for validation and export.

Below the table, the selected plan is shown by period: current headway, effective plan
headway after rounding, trips per direction, 18 m trips, and the desired against the actual
18 m share. On the thesis example at the default limits this is plan `43:1` — morning
10.59 min with 16 of 17 trips on 18 m vehicles, midday 12.86, evening 10.59 with all 17
trips on 18 m, off-peak 12.41 — with waiting 83 333 → 95 608 (+14.7 %), crowding disutility
237 329 → 125 300 (−47.2 %), their sum −31.1 %, and the cost index 2 252.8 → 2 752.0
(+22.2 %).

A higher waiting figure is shown as a worsening. The console never nets the two together
into a single "improvement" number.

### All calculated plans — the Pareto chart

Expand **All calculated plans** below the selected plan's timetable. An interactive
**Pareto front** chart replaces the table and shows all calculated candidates from the
three optimiser runs, without the previous 60-plan limit. The number of plans appears
above the chart.

- The horizontal axis is **Passenger waiting**, in pax·min/day.
- The vertical axis is **Crowding disutility**, in pax·min/day.
- Points further left and lower down have smaller values for these two measures.
  Larger points indicate a higher operating-cost index. This is a two-dimensional view
  of three objectives, so a point further left and lower down does not necessarily cost less.
- Named alternatives are dark blue and labelled **Recommended**, **Least waiting**,
  **Least crowding**, with their plan identifiers. The inspected plan is highlighted
  in orange with a ring.

Click a point to display its details below the chart. Where points are close together,
the nearest point to the cursor is selected; use **Inspect plan** for an exact choice,
including overlapping points. Hover over a point for a short tooltip. For keyboard
navigation, use `Tab` to focus the chart: right/down arrows select the next plan,
left/up arrows the previous plan, and `Home` and `End` the first and last plan in
selection-rule order. This order does not follow the points' positions from left to right.

The detail card shows the plan identifier and label, waiting, crowding disutility, their
sum, operating-cost index, longest headway, and **Selection-rule rank** — its position
in selection-rule order among all candidates.

The chart and detail card use model values **before rounding to whole trips**. These
may differ from the same plan's figures in the main comparison table, which uses the
executable timetable after rounding. Inspecting a point does not change the plan for
validation, the timetable by period, or the report. To proceed with validation, select
an alternative using its radio button in the main table and click **Continue to validation**.
If a warning says that conditions have changed, return to **Conditions** and click
**Recalculate plans** to obtain a chart for the new conditions.

---

## 5. Step 3 — Validate & export

Four blocks, deliberately kept apart.

### Model result

The operations model on the executable timetable — waiting, crowding disutility, cost
index, vehicles needed. It is not a simulation, and the block says so. Fleet availability
is reported as **not verified**, because the fleet check is normative.

### Simulation

Until a simulation of **this** plan is loaded, the block reads **Not simulated for this
plan**. If the plan exactly matches the accepted release, **View registered result** reads
that result without rerunning SUMO, after checking its input, plans, model, protocol and
30 + 30 run counts. Otherwise **Run full simulation** starts 30 + 30 SUMO runs for the
current timetable and this plan (both directions, 07:00–10:00 departure cohort, background
traffic, fixed seeds). This new series is exploratory for the edited plan and can take
several minutes; you can leave the page while it runs.

A simulation of a different plan is never presented as verification of this one. Earlier
published results are shown only as a labelled historical reference with their own metric
definition.

### Economics

Scopes are reported separately and unknowns stay unknown:

- morning peak (model) and whole day (model), each with the waiting and crowding
  components, at a waiting weight of 1× or 2×;
- in-vehicle time — included only when a simulation of this plan exists, otherwise stated
  as "not included";
- operator and fleet — the change in 12 m-equivalent km per year, vehicle-km and normative
  vehicle-hours per day. The money rate per vehicle-km is unknown, so **operator money cost
  and the combined balance are not computed**;
- monetisation — value of time 27 930 UZS per passenger-hour, 250 days per year
  (assumption), with a ±30 % sensitivity band that is not a confidence interval, and the
  money rate at which the whole-day model balance stays non-negative (93 019 UZS per
  12 m-equivalent km — not a profit or payback figure).

**Where 27,930 UZS per passenger-hour comes from.** The official quarterly
series of *gross nominal monthly wages* for Tashkent city gives
**11,684,262.5 UZS** for 2026 Q1 and **12,013,894.7 UZS** for Q2; Q1
is marked preliminary in the source workbook. The model averages them
to **11,849,078.6**, applies the **12 %** personal income-tax rate,
divides by the assumed **168 working hours per month** (21 days × 8
hours), and takes **45 %** of the resulting net hourly wage:

`((11,684,262.5 + 12,013,894.7) / 2) × 0.88 / 168 × 0.45 = 27,929.97 ≈ 27,930 UZS/passenger-hour`.

The wages and tax rate have external sources; **168 hours** and **45 %**
are model assumptions. An internal reference note proposes the 45 %
transfer for commuting time, but it has not been measured for Tashkent
passengers. The cited international review discusses roughly half the
*gross* wage, while this model uses 45 % of the wage *after tax*; these
are not identical measures. The official wage series excludes agriculture
and small businesses. **27,930 UZS** is the modelled value of one hour
saved by one passenger, not a fare, a payment to a passenger or operator
revenue.

**Why 250 days.** This is a fixed scenario count of days over which the
model repeats a daily effect. For example, annual waiting and crowding
benefit is daily passenger-hours saved × **250** × **27,929.97 UZS/hour**.
The project contains no derivation of 250 from a calendar or observed
travel data, so it must not be presented as a measured count of working
days. At 230 such days, the annual estimate would be 8 % lower, all else
equal. The **250 days per year** assumption is separate from **168 hours
per month**: one annualises a daily effect; the other converts monthly
wages to an hourly value.

On the thesis example: morning peak ≈ 5 345 million UZS/year, whole day ≈ 11 609 million,
both at a waiting weight of 1×.

### Re-optimise for each scenario

Re-runs the optimiser at each cost limit and each headway-spread limit. Each point may
select a **different** plan, so this is a sensitivity of the recommendation, not a
robustness test of one fixed plan — the block says so.

### Report

In **Report**, the equally sized **Preview report** and **Keep this plan in the list** buttons are side by side (stacked on narrow screens). Preview reads the complete report on screen before downloading; Keep adds the selected plan to the session list. Without a simulation, the preview button reads **Preview model-only report** and the report states that this plan has not been simulated.

Bold figures mark plan values and changes that improve on the current timetable, positive annual modelled passenger benefits, and key numeric inputs and outcomes in Passengers, Operator & fleet, and Monetisation. The report preview highlights the same economics figures as the section above. In Simulation, the status and both current and plan trip durations are bold, as on the main screen; the difference and confidence interval remain visible without implying a benefit from formatting alone. Cost increases and other worsening measures remain visible with their signs; unknown monetary costs remain unknown. The report retains the simulation's evidence status. The preview and downloaded report use the selected waiting-time weight (1× or 2×); changing it hides the old preview. Passenger benefit is not operator profit.

Below the preview, **Download report (HTML)** downloads the displayed report, **Download all files (ZIP)** downloads the complete bundle, and **Open report in new tab** opens the report separately. Preparing the preview saves the HTML, Markdown, JSON and CSV bundle in `exports/`; downloading starts when you follow a download link. Changing the plan, conditions or calculation results hides the previous preview; prepare it again for the current state.

---

## 6. What this prototype will not do

- It will not import operational data; every path is synthetic in this version.
- It will not call a plan verified on the strength of a simulation of another plan.
- It will not silently fall back to an archived result if SUMO is missing.
- It will not fill an unknown with a zero: the operator rate, the combined balance and
  fleet availability stay unknown until something measures them.
- It will not present the whole-day economics as including simulated in-vehicle time; only
  the morning cohort is simulated.

---

## 7. Timings on the reference machine

| Stage | Time |
|---|---|
| Generate a scenario (Layers 0–2) | ≈ 10 s |
| Load the thesis example | instant |
| Optimiser, three protocol seeds | ≈ 9 s |
| Re-optimise for each scenario | ≈ 1–2 min |
| Full simulation, 30 + 30 runs | ≈ 8–9 min with six workers |

Windows 11, Python 3.13.13, SUMO 1.27.0.

---

## 8. Sources for these explanations

- [Official quarterly wage series, indicator 2.02.02.1200](https://siat.stat.uz/data/522/?lang=en); saved copy: [D12_Tashkent_wages_quarterly_stat_uz.xlsx](../../07_Source_Data/D12_Tashkent_wages_quarterly_stat_uz.xlsx), `Ma'lumot` sheet, `AP210:AQ210`.
- [Personal income tax rates](https://gov.uz/en/advice/70/document/1325).
- [Kenneth A. Small, *Valuation of Travel Time* (2012)](https://sites.socsci.uci.edu/~ksmall/VOT%20review.pdf) provides international context, **not** a Tashkent estimate; [internal note on transferring value of time](../../07_Source_Data/D5_EU_VoT_transfer_base.docx).
- Model code: [new-scenario generation](../backend/pipeline.py), [demand-matrix validation](../../05_Pipeline/route51_service/layer2/validation.py), [optimiser and crowding settings](../../05_Pipeline/route51_service/service_config.yaml), [cost index and limit checks](../../05_Pipeline/route51_service/layer3/dispatch.py), [monetisation and 250 days](../../05_Pipeline/route51_service/layer5/economics.py).
