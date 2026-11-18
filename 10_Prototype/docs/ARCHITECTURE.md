# How the console uses the thesis pipeline

The console is a local, single-session application. The browser calls `backend/server.py`;
long work is tracked by `backend/jobs.py`; `backend/pipeline.py` imports the scientific
modules from `05_Pipeline/route51_service` and the Layer-0 generator. `backend/archive.py`
reads the accepted release and labels older tables as historical. Reports are written to
`10_Prototype/exports/`; job metadata and scratch files go to `10_Prototype/.work/`.

| User action | Endpoint | Actual work |
|---|---|---|
| Open thesis example | `POST /api/data/run` | Read the frozen matrix, calculate its quality against its synthetic oracle with `layer2.validation.validate`, fingerprint the input |
| Create a scenario | `POST /api/data/run` | Generate synthetic feeds, ingest them through the gateway, fuse demand, validate against the oracle |
| Calculate plans | `POST /api/plan/front` | Run the shared Layer-3 optimiser on three fixed seeds; round candidates to dispatch and check constraints |
| Select a plan | `POST /api/plan/select` | Select one of the calculated alternatives and display its executable schedule |
| View registered result | `POST /api/result/simulate` | If the accepted release matches the input, both plans, current model and protocol, validate its complete 30 + 30 analysis and load it without running SUMO |
| Run full simulation | `POST /api/result/simulate` | Otherwise run a new 30 + 30 SUMO series with the current dispatch; this new result is exploratory for the selected scenario |
| Inspect economics | `POST /api/result/economics` | Call `layer5.economics` with separate morning and whole-day scopes; include derived in-vehicle time only when the simulation passes the shared validator |
| Re-optimise for each scenario | `POST /api/result/holds` | Recalculate alternatives for each displayed budget and spread limit; this does not test one fixed plan's robustness |
| Download report | `POST /api/export` | Write HTML, Markdown, full-precision JSON, exact dispatches, period schedule and ZIP from the same session result |

`GET /api/config` reports dependencies and the accepted release. `GET /api/session`
restores the current in-memory state after a page refresh; `GET /api/jobs/{id}` reports
job progress in completed work units. A server restart loses the session, but a persisted
running job is reported as interrupted rather than polled forever. Hiding progress does
not cancel a calculation. Multiple independent users are outside this prototype's scope.

The result contract is implemented in
`05_Pipeline/route51_service/result_contracts.py`. A registered simulation is applicable
only if schema, input, baseline and candidate dispatch, model, protocol, complete measured
cohort and expected run counts match. `Engine.registered` checks the accepted release
before displaying it; `layer5.economics.simulation_ivt` uses the same validation before
monetising derived in-vehicle time. A missing SUMO installation prevents *new* runs but
does not prevent opening a matching, already accepted result. A different historical
result cannot confirm the current plan.

The model and assumptions live in the shared pipeline configuration and modules.
`settings.py` contains only UI choices such as supported demand scales and slider stops.
The page is static HTML, CSS and JavaScript; fonts are served locally. The interface
shows model outcomes, simulation evidence and economic scopes separately. Unknown
operator prices and operational fleet availability remain unknown in the report.
