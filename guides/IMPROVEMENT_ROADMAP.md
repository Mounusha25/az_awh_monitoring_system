# Improvement Roadmap — Operational System

> Scope: production monitoring system only — `awh_az/` (backend + dashboard) and
> `RPi_USB_Package/` (edge layer). Deliberately excludes `research_extension/`
> (Phases 1-4, anomaly attribution models, LangGraph agents) — that work is
> tracked separately and is out of scope for this document.
>
> Grounded against the current codebase as of 2026-08-27: production backend
> endpoints (`/stations`, `/stations/{name}/readings`, `/stations/{name}/hourly`,
> `/export`, `/stations-registry`, `/cache/*`), the dashboard's current component
> set (`StationCard`, `FeaturePlot`, `CSVExport`, `Header`, `Footer`), and past
> incidents (ingestion checkpoint desync, GCP billing outage, Redis never
> provisioned in prod).

---

## UI (dashboard)

- **Cross-station comparison view.** Every page today is single-station
  (`stations/[id]`). A grid comparing harvesting efficiency or hourly
  weight-delta across all active stations answers "which station needs
  attention" without clicking through each one.
- **Data-freshness indicator.** Past incidents (ingestion checkpoint desync,
  GCP billing outage) silently broke ingestion for stretches. A "last
  successful reading per station" badge on the stations list surfaces that in
  the UI instead of someone noticing days later.
- **Annotate `FeaturePlot` with simple threshold bands** — not ML, just
  sensor min/max sanity ranges (e.g., the weight-jitter noise floor already
  filtered in backend aggregation) shown visually so a field engineer can
  eyeball outliers without reading raw numbers.
- **Harvesting efficiency trend chart.** The formula is documented
  (`HARVESTING_EFFICIENCY_FORMULA.md`) but nothing currently plots it over
  time — this is the metric the lab actually cares about, more than raw
  sensor values.
- **Export UX.** `/export` exists as a POST endpoint but `CSVExport.tsx` is
  the only frontend surface for it; a date-range picker + per-station
  multi-select would make it usable without hitting the API directly.
- **Cache/registry admin surfaced somewhere.** `/cache/stats`,
  `/cache/invalidate`, `/cache/flush`, and the `/stations-registry` POST all
  exist as API-only ops right now with no UI; even a bare-bones internal admin
  page saves you from curl/Postman when debugging.

## New features

- **Maintenance log.** Several past incidents (frozen sensors, dead periods)
  were physical hardware problems indistinguishable from software bugs
  without knowing "was this station serviced." A simple table (station, date,
  note) tied to the registry would pay for itself the next time a sensor goes
  dead.
- **Alerting on ingestion gaps** — not sensor anomalies, just a scheduled
  check that pings you (email/Slack) if a station hasn't reported in N hours.
  Cheap to build, and would have caught the billing-outage and
  checkpoint-desync incidents faster.
- **Scheduled exports** (weekly CSV via email) instead of manual export
  calls.
- **Station config in the registry UI.** `/stations-registry` POST already
  supports creating stations; exposing it in the dashboard means adding a new
  station doesn't require a direct API call.

## Database

- **Actually use Postgres/TimescaleDB in the serving path.** Postgres/
  Timescale exist and are fed by `ingestion_worker.py`, but the live backend
  reads Firestore directly — Postgres isn't in the serving path at all right
  now. If `/hourly` aggregation ever gets slow as data grows (already past
  70K records), that's the fix: point `main.py`'s hourly/readings endpoints
  at Timescale instead of computing aggregation over Firestore docs on every
  request.
- **Index/partition check on `measurements`** once Postgres is actually
  serving traffic — the schema exists (`schema_timescaledb.sql`) but there's
  no evidence it's load-bearing yet.
- **Provision Redis for real, or explicitly commit to the in-process
  fallback as permanent.** Right now it's an implicit fallback rather than a
  decided architecture; worth deciding one way given multi-instance
  deployment on Render would make in-process caching inconsistent across
  instances.

---

## If picking three

1. **Cross-station comparison view** — the actual gap in a single-station-only UI.
2. **Ingestion-gap alerting** — directly prevents recurrence of incidents already hit twice.
3. **Wire Postgres/Timescale into the serving path** before it becomes a performance problem rather than after.
