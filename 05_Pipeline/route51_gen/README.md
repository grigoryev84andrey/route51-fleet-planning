# Route-51 Synthetic Multi-Feed Generator (Tashkent)

A reproducible generator that produces **three separate, independently-degradable
operator-style data feeds** for bus Route 51, all derived from one shared demand
substrate so they remain mutually consistent (required for the data-fusion
analysis). Decoupled architecture: **SUMO is the downstream optimizer-evaluation
engine only — not a data source.**

## Architecture

```
            ┌────────────────────────────────────────────┐
            │  SHARED DEMAND SUBSTRATE  (ground truth)     │
            │  persons → activity schedules → true trips   │
            │  → true OD, true board+alight, true positions│
            └───────────────┬───────────────┬─────────────┘
                            │               │              │
        ┌───────────────────▼──┐  ┌─────────▼────────┐  ┌──▼────────────────┐
        │ FEED 1  Telecom/CDR  │  │ FEED 2  ATTO fare│  │ FEED 3  Bus GPS   │
        │ all phone-owning     │  │ Route-51 riders  │  │ timetable-driven  │
        │ movers → signalling  │  │ tap-IN only,     │  │ positions+speed+  │
        │ events → recovered OD│  │ NO alighting     │  │ occupancy         │
        └──────────┬───────────┘  └──────────────────┘  └───────────────────┘
                   │ each feed has its OWN degradation block (default: clean)
                   ▼
        recovered OD  vs  ground-truth OD  =  error as a FUNCTION of degradation
```

## Degradation taxonomy (config: `degradation:` + `sweep:`)

Baseline = clean. Dial each knob to approach real data; sweep to report a band.

| Knob | Feed | Param | Source |
|---|---|---|---|
| Phone-ownership skew | Telecom | `penetration` (~0.73 unique-user, demo-coupled) + single-`observed_operator` sampling (20–28%, urban/income skew) + `multi_sim` | D1 §5.1–5.2 |
| Data loss / partial | Telecom | `p_missing` | v2 |
| | ATTO | `p_untapped` (evasion, enforcement-biased) + `inactive_card_rate` | D2 §6.5 |
| | GPS | `p_gps_dropout` | — |
| Thin out CDR | Telecom | `event_rate_multiplier` × `usage_intensity` (state-dependent) | v2 #2 |
| Degrade geoposition | Telecom | `p_oscillation` (density-scaled `grid_disk` spread) | v2 #3 |
| | GPS | `gps_jitter_m` | — |
| Open-loop ID fragmentation | ATTO | `token_fragmentation` (1 person → many hashed IDs; breaks trip-chaining) | D2 §6.3 |

## Calibration provenance & LOAD-BEARING caveats

All calibration lives in `config.yaml`, tagged `[D1] [D2] [D10]`, with every
non-measured value labelled `PLACEHOLDER` (load-time warning lists them).

- **D1 (telecom):** Uzbekistan has **no published per-operator split**; four
  operators ~20–28% each, no clear leader. Operator weights are a **sensitivity
  parameter**, swept across band endpoints — never a point estimate. Penetration
  is **not uniform** across operators/demographics (the "proportional
  penetration" assumption is the questionable, load-bearing claim). Base persons
  on **unique subscribers, not SIMs** (~1 in 4–5 SIMs is secondary).
- **D2 (ATTO):** ATTO is the **payment-system operator**, distinct from the bus
  carrier **Toshshahartrans (TSTX)** — **do not conflate**. Tap-**in only, no
  alighting** on both modes → destination must be inferred. Data window is
  **2025–2026 flat-fare, pre-distance-fare** (2027 metro decree out of scope;
  buses unaffected). Transaction ≠ journey; AFC coverage near-complete but not
  total (fare evasion).
- **Stop catalogue (`data/Busstations.csv`):** the 23 stops S01..S23 with their
  WGS84 coordinates were **compiled by the author in late spring 2026** from the
  Route-51 listings published by the **2GIS** and **Yandex Maps** services and
  cross-checked between the two. They are **not** a GTFS extract and not an
  operator hand-off, so the stop set reflects those services' view of the route
  at that date. Internal check: the straight-line sum through the coordinates is
  11.91 km against the 12.8 km on-road route length — consistent with on-road
  routing. Direction is declared in `config.yaml` as `S23_to_S01`
  (Yunusabad → Beshagach); the file itself carries no `seq` column, so order is
  the S01..S23 numbering.
- **D10 (demand):** District resolution (12 zones) is **too coarse**;
  dasymetric/Bayesian **downscaling onto H3 is mandatory and load-bearing** —
  it needs OSM landuse=residential / building footprints, **not yet available**,
  so home placement currently uses a PLACEHOLDER density surface. Registered
  (propiska) ≠ daytime population; periphery biased low, centre pull understated.
  Corridor = production-NE (Yunusabad) → attraction-centre, Pakhtakor as the
  on-corridor transfer anchor; Novza is an out-of-catchment (cordon) gateway.

## Status

| Stage | Module | State |
|---|---|---|
| 0 | `config.yaml`, `src/config.py`, `src/geo.py`, `run_geo_check.py` | **DONE** — calibrated config + H3 corridor on the real 23 stops; anchors/gateways snap & verify |
| 1 | `population.py` — synth persons (demo, phone+operator, ATTO card) | **DONE** — full catchment population (operator as per-person attribute); 80k in ~3s |
| 2 | `activities.py` — schedules + gravity destination choice → ground-truth trips/OD | **DONE** — Home→primary→shop→home, gravity choice, mode/route, **ground-truth cell OD + stop-level bus OD** (the oracle) |
| 3 | `feed_telecom.py` — signalling events (LAU/HO/TA) + degradation | **DONE** — derived from observed-operator subscribers; coarse serving cell, no coords; 524k events from 11k subs; jitter/dropout/thinning/multi-sim knobs |
| 4 | `feed_atto.py` — tap-in events (no alight) + degradation | **DONE** — boarding-only from bus riders; 64-hex hashed cards; evasion/inactive/token-fragmentation knobs |
| 5 | `feed_gps.py` — timetable-driven bus telemetry + degradation | **DONE** — 142 weekday trips, 12/12 buses, one-way calibrated to 41.0 min, jitter/dropout/clock knobs verified; **occupancy is PLACEHOLDER until Stages 1-2 wire demand** |
| 6 | `sweep.py` + `validation_report.md` — error as a function of degradation | next |

## Run

```bash
pip install -r requirements.txt
python run_geo_check.py        # Stage-0 verification + corridor_cells.geojson
```

## Known dependencies / open items
- OSM landuse=residential / building footprints → real production downscaling (D10 §6.1).
- OSM POI density per H3 → real attraction weights (D10 §5).
- stat.uz tuman marginals → replace demographic placeholders.
- Confirm single-ride fare (1,700 vs 3,000 UZS) and operator-leadership camp (D1/D2 verify-notes).
- ATTO account-level (not token-level) hashed IDs → robustness of card-ID trip-chaining (D2 §6.3).
