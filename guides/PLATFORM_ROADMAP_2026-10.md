# AzAWH Improvement Roadmap

> Prioritized plan for making the platform more reliable and for adding features.
> Written 2026-10-01 from CLAUDE.md and project history (not a fresh code audit).
> Principle: fix reliability and operations first, then add features. Most past
> incidents (two ingestion desyncs, the GCP billing outage, Render health-check
> timeouts, 502s from blocking calls, diverged repos) were operational, not missing features.

---

## 1. Foundations (make it smooth)

### 1.1 One source of truth for data
- Today `/readings` and `/hourly` read Postgres, while `/stations`, `/impact` and
  `/export` read Firestore. This hybrid is why exports are slow and why ingestion
  desyncs mattered.
- Serve everything from Postgres (or BigQuery / Cloud SQL on GCP). Keep Firestore
  only as the edge write buffer.
- Replace the polling worker + JSON checkpoint with an event-driven path
  (Firestore -> Pub/Sub -> Dataflow or Cloud Run job) with idempotent inserts.
  The checkpoint file desynced twice (~960K rows missing the second time).
- Add a nightly reconciliation job comparing Firestore and Postgres row counts per
  station, alerting on any mismatch. Automates the manual August recovery check.

### 1.2 Collapse the deploy process
- DONE 2026-10-02: Render now deploys from `azawhasu-team/AzAWH-Project`, so one
  push ships both services. Push is automated by `scripts/push_to_main_repo.py`.
  (Previously a second push to `Mounusha25/az_awh_monitoring_system` was needed.)
- Add CI (GitHub Actions): lint, type-check, tests, preview deploy per PR.
- Add infrastructure as code (Terraform) so the GCP project, billing and Firebase
  setup are reproducible.

### 1.3 Observability and alerting
- Structured logging; Prometheus/Grafana or Cloud Monitoring; uptime check on `/health`.
- GCP billing budget alert (the 2026-08-05 outage was a free trial expiring unnoticed).
- Per-station data-freshness alerts ("station N silent for 30 min"). Currently 8 of
  9 stations being inactive is only visible if someone looks.

### 1.4 Edge (Raspberry Pi) reliability
- Local store-and-forward queue (SQLite) so network drops do not lose readings.
- systemd services with a watchdog; OTA config updates.
- Remove duplicate scripts (`read_power.py` vs `read_power_new.py`) to prevent
  Mac/Pi drift and false debugging alarms.
- Device heartbeats (CPU temp, disk, last successful sensor read) alongside sensor data.

### 1.5 Cache, tests, exports
- Provision real Redis (Memorystore), or formally document the in-process fallback.
- Generate TypeScript types from the backend OpenAPI schema; add contract tests so
  Pydantic models and dashboard types cannot drift.
- Load-test `/export`. Stream large exports to Cloud Storage and return a signed
  URL instead of holding a request open.

---

## 2. Features to add

### 2.1 Operational (highest user value)
- **Station health page:** sensor-level status, last-seen, dead/frozen sensor
  detection (promote the Phase 2 frozen-sensor logic into production).
- **Real alerting:** email / Slack / SMS with acknowledge and snooze (the
  escalation agent currently only logs).
- **Station onboarding UI:** register a station, set `intake_area_m2` and
  calibration without code edits.
- **Roles and audit log:** OAuth/SSO with ASU accounts.
- **Reporting:** public impact page (lifetime water harvested) and scheduled
  PDF/CSV reports for lab stakeholders.

### 2.2 Analytics
- Weather/context overlay: expected vs actual harvest per station.
- Calibration management: record recalibration dates so drift is not confused
  with real change.
- Cross-station comparison and fleet-level benchmarks.
- Daily per-station data-quality scores.

### 2.3 Research track
- Treat the 0.415 attribution F1 as a label/data-scope problem, not a model-choice problem:
  - Obtain a small human-labeled set from the lab (~200 events).
  - Add physics-based features (dew point, psychrometrics, energy per liter).
  - Consider reframing RQ1 if attribution is ill-posed with weak labels.
- Evaluate with calibration (reliability curves) and time-blocked CV.
- Phase 4: run Evidently drift reports as a scheduled Cloud Run job; Airflow is
  likely overkill at current scale.
- Run the Phase 3 `IncidentReportAgent` with a real LLM call before making any RQ3 claims.

---

## 3. Suggested order of work

1. Single repo, CI, uptime/freshness/billing alerts (cheap, removes most recurring incidents).
2. One data path with automated reconciliation.
3. Edge store-and-forward and station health page.
4. Real notifications and Cloud Storage exports.
5. Research Phase 4 and the labeled evaluation set.

Best payoff for least effort: the freshness/health monitor and the
Firestore-vs-Postgres reconciliation script.
