# Data Schema Documentation
## Tashkent Bus Route Optimization Prototype
### Simulation Date: 2024-03-12 (Tuesday, baseline period)

---

> ## Scope of this directory — read first
>
> These files exist to **document the Layer-0 data contracts**: what each feed
> looks like, which columns it carries, and how the feeds join. They are a
> schema illustration.
>
> **They are not the inputs to Chapter 3.** No number or figure in the thesis
> comes from this directory. Chapter 3 is produced entirely by
> `3_Pipeline_and_Results/`, which reads its stop catalogue from
> `route51_gen/data/Busstations.csv` — a different and larger representation of
> Line 51 (23 stops against the 12 here, 12 vehicles against 5).
>
> The only element shared with the thesis is the baseline headway set in
> `gtfs_baseline_headways.csv`. See the package README for the same note.

---

## File 1: `route_stops.csv` — Illustrative Stop List (schema demo)

| Column | Type | Description |
|--------|------|-------------|
| stop_id | string | Unique stop identifier (R51_XX) |
| name | string | Stop name in transliterated Uzbek |
| route_id | string | Route number ("51") |
| seq | int | Stop sequence position on route |
| lat | float | WGS84 latitude |
| lon | float | WGS84 longitude |
| h3_level9 | string | **Pseudo**-H3 placeholder at the resolution-9 slot |
| h3_level7 | string | **Pseudo**-H3 placeholder at the resolution-7 slot |

12 stops, ordered SW to NE. Two cautions before this file is used for anything
beyond reading the column layout:

- The stop set is **not** the Line 51 catalogue the thesis models. It carries
  12 points named after metro stations, and its southern end runs towards
  Sergeli and Olmazor — 2.0 and 2.9 km from the nearest stop in the 23-stop
  catalogue. Total straight-line length is 10.6 km against 11.9 km there.
  For the stops the thesis actually uses, see `route51_gen/data/Busstations.csv`.
- The two H3 columns are **not valid H3 cell indices**. They are produced by
  `lat_lon_to_pseudo_h3()` in `generate_synthetic_data.py`, which fabricates
  identifier-shaped strings so the column layout can be shown; the `h3` library
  rejects all of them. Real indexing is done with `h3.latlng_to_cell()` in
  `route51_gen/src/` and `route51_service/layer1/`.

---

## File 2: `fleet_catalog.csv` — Vehicle Types and Capacities

| Column | Type | Description |
|--------|------|-------------|
| vehicle_id | string | Unique bus identifier |
| route_id | string | Assigned route |
| type | string | midi_8m / standard_12m / articulated_18m |
| capacity | int | Maximum passengers (standing + seated) |
| seated | int | Seated capacity only |

Vehicle types reflect real Tashkent fleet composition:
- midi_8m: 50 pax, used on lower-demand segments
- standard_12m: 85 pax, workhorse vehicle
- articulated_18m: 120 pax, used on highest-demand corridors

---

## File 3: `gtfs_baseline_headways.csv` — March 2024 Static Timetable

| Column | Type | Description |
|--------|------|-------------|
| route_id | string | Route number |
| period | string | Time period name |
| start | string | Period start (HH:MM) |
| end | string | Period end (HH:MM) |
| headway_min | int | Minutes between consecutive buses |

This is the baseline against which AI-optimized schedules are compared.

---

## File 4: `telecom_od_matrix.csv` — Hourly Population Flows

| Column | Type | Description |
|--------|------|-------------|
| timestamp | ISO 8601 | Hour start (YYYY-MM-DDTHH:MM:SS) |
| origin_h3_level9 | string | Origin hexagon H3 index (res 9) |
| destination_h3_level9 | string | Destination hexagon H3 index (res 9) |
| population_count | int | Estimated people moving between hexes |
| aggregation_period_hours | int | Always 1 (hourly aggregation) |
| data_source | string | "telecom_proxy" for synthetic data |

Key properties:
- Captures ALL population movement, not just bus riders (includes drivers, walkers).
- This is the "latent demand" signal that ATTO cannot see.
- Coarse spatial resolution (~174m hexagons) requires Bayesian downscaling to map onto 1D bus line.
- When real telecom data arrives, swap this file; schema stays identical.

---

## File 5: `atto_boarding_events.csv` — Smart Card Tap-In Logs

| Column | Type | Description |
|--------|------|-------------|
| hashed_card_id | string | SHA-256 hash of card ID (16 hex chars) |
| timestamp | ISO 8601 | Boarding time |
| bus_stop_id | string | Stop where passenger boarded |
| route_id | string | Route the passenger boarded |
| tariff_type | string | standard / student / pensioner / social |

Critical design constraint: NO alighting stop column exists.
This is the "alighting blindness" problem that the AI must solve
via trip-chaining inference (next boarding location or home anchor).

Tariff distribution: ~55% standard, ~20% student, ~15% pensioner, ~10% social.

---

## File 6: `bus_gps_telemetry.csv` — Vehicle Position Pings

| Column | Type | Description |
|--------|------|-------------|
| vehicle_id | string | Bus identifier (matches fleet_catalog) |
| timestamp | ISO 8601 | Ping time (every 30 seconds) |
| latitude | float | WGS84 latitude (with GPS jitter) |
| longitude | float | WGS84 longitude (with GPS jitter) |
| speed_kmh | float | Current speed in km/h |
| current_occupancy | int | Passengers currently on board |
| capacity | int | Vehicle max capacity |
| route_id | string | Route being served |

Operating hours: 05:30 - 23:00.
Speed patterns: slower in city center (congestion), slower during peak hours.
Occupancy correlates with demand profile and stop centrality.

---

## Data Relationships

```
telecom_od_matrix.origin_h3_level9 ──> route_stops.h3_level9
telecom_od_matrix.destination_h3_level9 ──> route_stops.h3_level9

atto_boarding_events.bus_stop_id ──> route_stops.stop_id
atto_boarding_events.route_id ──> route_stops.route_id

bus_gps_telemetry.vehicle_id ──> fleet_catalog.vehicle_id
bus_gps_telemetry.route_id ──> fleet_catalog.route_id

gtfs_baseline_headways.route_id ──> route_stops.route_id
```

## Plug-and-Play Contract

When real data becomes available from IT Park / Digital Transformation Center:
1. Replace `telecom_od_matrix.csv` with real Mobiuz/Beeline/Ucell weekly batch
2. Replace `atto_boarding_events.csv` with real ATTO daily transaction logs
3. Replace `bus_gps_telemetry.csv` with real onboard GPS logs
4. Schema columns stay identical; only `data_source` field changes

No code changes required in downstream modules.

---

## Scope note — route 51 only

These snapshots originally covered bus routes 51 and 131. The study is scoped to a
single corridor (Line 51), so every route-131 record has been removed: 5 headway rows,
13 stops, 5 vehicles, 2,186 ATTO boarding events and 10,500 GPS pings. The surviving
route-51 records are byte-identical to the original two-route extract — the files were
filtered, not regenerated, so Line 51's demand scale is unchanged.

`telecom_od_matrix.csv` was not touched: it has no route column, and the "131" values
in it are population counts.

`generate_synthetic_data.py` has been scoped to route 51 to match. Its ATTO target was
lowered from 4,500 to 2,300 daily boardings at the same time: the target is a city-wide
total spread over whatever stops exist, so leaving it at 4,500 with one route removed
would have concentrated two routes' demand onto Line 51 and doubled its boardings.

A verbatim pre-edit copy is kept in `../data_backup_with131/`.
