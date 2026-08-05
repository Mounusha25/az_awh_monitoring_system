# CLAUDE.md — AzAWH Monitoring System
> **For AI assistants and new contributors:** This file is the single source of truth
> for understanding this entire project — what it is, what it does, what has been built,
> what is being built next, and why every architectural decision was made.
> Read this before touching any file.

---

## 1. Project Identity

**Full name:** AzAWH — Arizona Atmospheric Water Harvesting Monitoring System  
**Lab:** SSEBE (School of Sustainable Engineering and the Built Environment), Arizona State University  
**Owner/Author:** Mounusha Ram Metti — MS Data Science, ASU (GPA 4.0, Expected Dec 2026)  
**Contact:** mmetti@asu.edu | mounushametti.vercel.app | github.com/Mounusha25  
**Status as of May 2026:** Production operational — 70,000+ real sensor records across
8 active monitoring stations and 14 environmental parameters. Research extension actively in development.

---

## 2. What This Project Is

This is a **full-stack cyber-physical environmental monitoring platform** for
Atmospheric Water Harvesting (AWH) stations deployed at ASU. AWH stations extract
liquid water directly from humid air using fan-driven airflow over a desiccant or
cooling surface.

This system does three things:
1. **Collects** — Raspberry Pi scripts at each physical station read hardware sensors
   (anemometers, flow meters, balances, power meters, pump controllers) and write
   data to Firebase Firestore in real time
2. **Stores and serves** — A Firebase → PostgreSQL ingestion worker moves data into
   a relational/time-series database; a FastAPI backend exposes that data as a
   typed, cached REST API
3. **Visualizes** — A Next.js dashboard (separate repo: `az_awh_dashboard`) consumes
   the API and renders per-station analytics, time-series charts, harvesting efficiency
   metrics, and data exports

This is **not** a data science prototype. It is a production monitoring system that
has been running live since October 2025 with real hardware, real data, and real
operational use by the SSEBE lab.

---

## 3. Research Objective and Vision

### The Problem
Environmental sensor networks generate continuous multi-parameter data streams.
A single anomaly in one sensor parameter could be sensor noise OR a real physical
event. Current systems evaluate parameters independently via static rule-based
thresholds — producing high false-alert rates and providing no attribution of
which sensor caused the anomaly versus which merely responded to it.

### The Vision
Transform this operational monitoring platform into an **Autonomous Environmental
Sensor Intelligence system** — a multi-agent MLOps framework that:
- Detects multi-parameter co-anomalies across all 14 sensor parameters simultaneously
- Attributes anomalies to their causal sensor parameter with confidence calibration
- Adapts automatically to sensor calibration drift through automated retraining pipelines
- Generates natural-language incident summaries for non-specialist field engineers
  and regulatory stakeholders using RAG-grounded LLMs

### The Three Research Questions
- **RQ1 — Attribution accuracy:** Can a LangGraph-orchestrated multi-agent system
  (LSTM + Isolation Forest) achieve F1 > 0.80 on anomaly attribution where
  rule-based baselines achieve F1 < 0.65?
- **RQ2 — Drift-adaptive retraining:** Can an Evidently AI-monitored,
  Airflow-orchestrated retraining pipeline recover to within 5% of pre-drift F1
  within 48 hours of drift detection?
- **RQ3 — Stakeholder response quality:** Do LLM-generated incident summaries
  enable correct intervention decisions at least 25% faster than numeric alert tables?

### Publication Target
**Environmental Modelling & Software** (Elsevier, IF 4.9)  
Working title: *Confidence-Calibrated Anomaly Attribution in Multi-Parameter
Environmental Sensor Networks: A Multi-Agent MLOps Framework with Drift-Adaptive Retraining*  
Preprint: arXiv (cs.LG + eess.SP) on system completion

---

## 4. Repository Structure

### This repo: `az_awh_monitoring_system`

```
az_awh_monitoring_system/
│
├── CLAUDE.md                          ← YOU ARE HERE — full project context
│
├── ingestion_worker.py                ← Firebase → PostgreSQL ETL pipeline (production)
├── requirements_ingestion.txt         ← Dependencies for ingestion worker only
├── read_env_anemometer.py             ← Standalone anemometer diagnostic script
├── prepare_for_rpi.sh                 ← Shell script to prep code for Raspberry Pi
│
├── schema_postgresql_simple.sql       ← PostgreSQL schema (no TimescaleDB)
├── schema_timescaledb.sql             ← TimescaleDB hypertable schema
│
├── awh_az/                            ← Main application package
│   ├── README.md                      ← Setup and run instructions
│   ├── PROJECT_GUIDE.md               ← Architecture and operational walkthrough
│   ├── CHANGELOG.md                   ← Version history
│   │
│   └── backend/                       ← FastAPI application (production service)
│       ├── main.py                    ← API routes, endpoints, app entry point
│       ├── models.py                  ← Pydantic models (StationReading, etc.)
│       ├── config.py                  ← Environment-driven configuration
│       ├── cache.py                   ← Redis caching layer
│       ├── datadownload.py            ← CSV/JSON export logic
│       ├── requirements.txt           ← Backend Python dependencies
│       ├── Dockerfile                 ← Container definition
│       ├── render.yaml                ← Render.com deployment config
│       ├── .env.example               ← Required environment variables template
│       └── REDIS_CACHING.md           ← Redis setup and caching documentation
│
├── RPi_USB_Package/                   ← Raspberry Pi hardware edge layer (flat layout — no subfolders)
│   ├── AquaPars1.py                   ← Main station runtime / orchestrator (uses read_power.py)
│   ├── AquaPars1_new_pm.py            ← Station runtime variant (uses read_power_new.py / DEM730P Modbus)
│   ├── awh_ui_layout.py               ← Local operator UI on the Pi
│   ├── intake_anemometer.py           ← Intake air: temp, humidity, velocity (auto-detects CP2102 by-id)
│   ├── outtake_anemometer.py          ← Outtake air: temp, humidity, velocity (auto-detects CP2102 by-id)
│   ├── read_balance.py                ← Water weight from balance scale (auto-detects Prolific/Dtech by-id)
│   ├── read_flow.py                   ← Flow rate via GPIO pulse counting (GPIO 27)
│   ├── read_power.py                  ← Older power meter reader (Prolific adapter, hardcoded path)
│   ├── read_power_new.py              ← DEM730P power meter via RS485 Modbus RTU (auto-detects FTDI by-id)
│   ├── pump_controller.py             ← Pump on/off control
│   ├── RASPBERRY_PI_COMMANDS.txt      ← Pi-specific setup and operational commands
│   ├── test_balance.py                ← Individual sensor test script
│   ├── test_flow.py                   ← Individual sensor test script
│   ├── test_pump.py                   ← Individual sensor test script
│   ├── test_powermeter.py             ← Tests read_power.py
│   ├── test_powermeter_new.py         ← Tests read_power_new.py
│   ├── test_intake_anememoter.py      ← Tests intake_anemometer.py (filename typo, kept as-is on purpose)
│   ├── test_outtaketake_anememoter.py ← Tests outtake_anemometer.py (filename typo, kept as-is on purpose)
│   ├── debug_powermeter.py            ← Raw Modbus byte-level diagnostic for DEM730P
│   ├── scan_powermeter.py             ← Modbus address/baud discovery for DEM730P (minimalmodbus)
│   └── scan_powermeter_manual.py      ← Same address/baud sweep, no minimalmodbus dependency
│
└── guides/                            ← Human-readable documentation hub
    ├── ARCHITECTURE.md
    ├── INGESTION_GUIDE.md
    ├── UI_GUIDE.md
    ├── HARVESTING_EFFICIENCY_FORMULA.md
    ├── DEPLOYMENT_GUIDE.md
    ├── STATUS_REPORT.md
    └── QUICK_REFERENCE.md
```

### Companion repo: `az_awh_dashboard`

```
az_awh_dashboard/
├── src/
│   ├── app/           ← Next.js App Router pages (station pages, exports, overview)
│   ├── components/    ← Reusable React UI components (charts, cards, tables)
│   ├── lib/           ← API client utilities, data fetching helpers
│   ├── data/          ← Static reference data (station configs, thresholds)
│   └── types/         ← TypeScript interfaces (mirrors backend Pydantic models)
├── sample-data/       ← Sample sensor data for local dev without live backend
├── STATION_MANAGEMENT.md
└── package.json       ← Next.js 15, React 19, TypeScript, Tailwind CSS
```

---

## 5. Data Model — The 14 Sensor Parameters

Every measurement record carries these fields. This is the vocabulary of the project.

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `timestamp` | ISO 8601 | Station runtime | When the reading was taken |
| `station_id` | int (FK) | Database | Links to `stations` table |
| `temperature` | float °C | Intake anemometer (ttyUSB2) | Intake air temperature |
| `humidity` | float % | Intake anemometer (ttyUSB2) | Intake air relative humidity |
| `velocity` | float m/s | Intake anemometer (ttyUSB2) | Intake airflow velocity |
| `unit` | string | Intake anemometer | Velocity unit string |
| `outtake_temperature` | float °C | Outtake anemometer (ttyUSB3) | Outtake air temperature |
| `outtake_humidity` | float % | Outtake anemometer (ttyUSB3) | Outtake air relative humidity |
| `outtake_velocity` | float m/s | Outtake anemometer (ttyUSB3) | Outtake airflow velocity |
| `outtake_unit` | string | Outtake anemometer | Velocity unit string |
| `weight` | float g | Balance (ttyUSB0) | Water collected (delta = harvest rate) |
| `pump_status` | string/int | Pump controller | Pump operational state |
| `voltage` | float V | Power meter | Supply voltage |
| `power` | float W | Power meter | Instantaneous power draw |
| `energy` | float kWh | Power meter | Cumulative energy consumed |
| `flow_lmin` | float L/min | Flow meter (GPIO 27) | Water flow rate |
| `flow_hz` | float Hz | Flow meter (GPIO 27) | Raw pulse frequency |
| `flow_total` | float L | Flow meter (GPIO 27) | Cumulative volume |

**Important:** All fields are `Optional` / `None`-able in `models.py` because
different stations may not have all sensors installed. The system is designed
for heterogeneous hardware configurations.

---

## 6. Hardware Layer — Raspberry Pi Station

Each AWH station runs a Raspberry Pi with `RPi_USB_Package/`. USB port assignments:

| Device | Port | Protocol |
|--------|------|----------|
| Balance scale | `/dev/ttyUSB0` | Serial 9600 baud, ASCII (`ST,GS,-0.123`) |
| Power meter | `/dev/ttyUSB1` | Serial |
| Intake anemometer | `/dev/ttyUSB2` | Serial 9600 baud, binary (`\xeb\xa0` header, 16 bytes) |
| Outtake anemometer | `/dev/ttyUSB3` | Serial 9600 baud, binary (`\xeb\xa0` header, 16 bytes) |
| Flow meter | GPIO pin 27 | Pulse output, open-collector (pull_up=True), K=2800 pulses/L |

**Anemometer packet format:**
`\xeb\xa0` + byte3 (mode) + byte4 (unit) + 2B humidity + 2B temperature + 6B velocity
(3×16-bit values averaged with scale factor 1.53 / (0x98 * 3 / 3))

**Known hardware notes:**
- Run `sudo usermod -a -G dialout $USER` once for permanent serial port access
- Use udev rules for stable port names: `/etc/udev/rules.d/99-awh-sensors.rules`
- Flow sensor requires active water flow to produce non-zero readings
- Anemometers must be physically powered ON and in continuous output mode
- If `ttyUSB` ports shift after reboot, run `dmesg | grep ttyUSB` to remap

---

## 7. Data Pipeline — End to End

```
[Physical AWH Station]
        │
        │  Hardware sensors → Python scripts (RPi_USB_Package/)
        ▼
[Firebase Firestore]
  Collection: measurements
  Document fields: timestamp, station_name, all 14 sensor params
        │
        │  ingestion_worker.py
        │  Polls every 60s, batch 500 docs
        │  Checkpoint: /var/lib/awh-ingestion/checkpoint.json (atomic write)
        │  Idempotency: ON CONFLICT (time, station_id) DO NOTHING
        ▼
[PostgreSQL / TimescaleDB]
  Tables: stations, measurements
        │
        │  Redis cache (cache.py) — hot query results, reduces DB load
        ▼
[FastAPI Backend — awh_az/backend/main.py]
  GET  /stations               → list all stations
  GET  /stations/{id}/readings → paginated raw readings
  GET  /stations/{id}/hourly   → hourly aggregated values
  GET  /health                 → service health check
  POST /export                 → CSV/JSON bulk export
        │
        │  HTTP REST API
        ▼
[Next.js Dashboard — az_awh_dashboard]
  Station overview, time-series charts, efficiency metrics, data exports
```

---

## 8. Current Tech Stack (Operational as of May 2026)

### Station / Edge Layer
- **Python 3** — all sensor scripts
- **pyserial** — USB serial communication with anemometers and balance
- **gpiozero + lgpio** — GPIO pulse counting for flow meter
- **Tkinter** — local operator UI (`awh_ui_layout.py`)
- **Firebase Admin SDK** — writes sensor readings to Firestore

### Ingestion Layer
- **firebase-admin 6.2** — Firestore polling
- **psycopg2** — PostgreSQL writes
- **Atomic checkpoint pattern** — JSON file with `os.rename()` for crash safety

### Backend / API Layer
- **FastAPI 0.115** + **Uvicorn** — REST API and ASGI server
- **Pydantic v2** + **pydantic-settings** — schema validation and env config
- **Redis 5** — response caching
- **firebase-admin 6.6** — cloud storage access
- **pandas + pyarrow** — data processing and Parquet export
- **Docker** + **Render.com** — containerized cloud deployment

### Frontend / Dashboard
- **Next.js 15** (App Router) + **React 19** + **TypeScript** + **Tailwind CSS**

### Database
- **PostgreSQL** — primary relational store
- **TimescaleDB** — time-series extension (hypertable on `measurements.time`)

---

## 9. Research Extension Tech Stack (Summer 2026 — In Development)

Phases 1–3 are built and have been run at least once; Phases 4–5 are NOT yet in
the codebase and remain the next phases per the research proposal.

### Phase 1 (Weeks 1–2) — Streaming Foundation ✅ Built
Lives in `research_extension/phase1_streaming/`:
- `producer.py` — Kafka producer; replays PostgreSQL sensor rows in
  chronological order (`--mode replay|live`, `--speed`, `--since`) to topic
  `awh-sensor-readings`
- `consumer.py` — Spark Structured Streaming consumer; computes windowed
  features (mean/std/min/max, 30-min sliding window, 5-min step) and writes
  to the `windowed_features` table
- `schema_windowed_features.sql` — target table schema
- `mlflow_setup.py` — creates the `AWH-AnomalyDetection` MLflow experiment
- `docker-compose.yml` — local Kafka broker
- `requirements_phase1.txt` — kafka-python, pyspark, mlflow, psycopg2, pandas
  (resolved for Python 3.14 / macOS arm64; needs Java 11 for pyspark)
- **Apache Kafka** — simulated real-time sensor event streaming
- **Apache Spark Structured Streaming** — windowed feature aggregation
  (30-min sliding window, 5-min step) → writes to `windowed_features` table
- **MLflow** — experiment tracking setup

### Phase 2 (Weeks 3–4) — Anomaly Detection Models ✅ Built
Lives in `research_extension/phase2_models/`. 14 rounds of iteration are logged
in `PENDING_TASKS.md` Part C — highlights: a real data-pipeline bug (dead/frozen
sensors silently contaminating the "clean" baseline, fixed round 10–11), an
8x real-station expansion after finding the local Postgres mirror was stale
(round 12), and scoping the benchmark down to the 4 features the lab actually
uses (round 13), which roughly doubled every model's score.
- **PyTorch** — LSTM temporal anomaly/attribution model (`lstm_attribution_model.py`) — current best, attribution F1 = 0.415
- **scikit-learn** — Isolation Forest ensemble (`isolation_forest_model.py`), supervised RandomForest classifier, two-stage model, rule baseline
- **MLflow** — experiment tracking (`AWH-AnomalyDetection`, same tracking DB as Phase 1)
- Target: F1 > 0.80 on held-out test set (rule-based baseline: F1 < 0.65) — **not yet reached**; 0.415 is the current honest ceiling after 14 rounds across 5 model families. See [[research_phase2_attribution_status]] memory / PENDING_TASKS.md for the full history before attempting further model changes.

### Phase 3 (Weeks 4–5) — Multi-Agent Orchestration ✅ Built
Lives in `research_extension/phase3_agents/`. Orchestrates the Phase 2 models
(Isolation Forest ensemble) rather than replacing them — `run_pipeline.py` is
the demo entry point.
- **LangGraph** — `StateGraph` with parallel fan-out/fan-in + conditional
  early-exit (most windows are normal — the graph skips the LLM call
  entirely rather than reaching the incident/escalation nodes)
- **Agents:**
  - `SensorDriftAgent` — wraps the saved Isolation Forest ensemble to flag anomalous windows and attribute a likely cause
  - `ThresholdBreachAgent` — checks raw readings against **placeholder operational sanity bounds**, not real EPA data (AWH telemetry doesn't map to standard EPA drinking-water limits — explicit decision 2026-07-31; swap in real values if this needs to answer to an actual regulatory framework)
  - `IncidentReportAgent` — RAG-grounded Claude (`claude-opus-5`) call; retrieval is keyword overlap over `guides/*.md` (not a vector DB — the corpus is ~3K lines, keyword ranking is sufficient)
  - `StakeholderEscalationAgent` — decides severity + routing and **logs the decision only** — no real notification channel wired up (explicit decision 2026-07-31)
- Requires `ANTHROPIC_API_KEY` (or `ant auth login`) to actually run the `IncidentReportAgent` LLM call — every other node runs with no external credentials.

### Phase 4 (Weeks 6–7) — Drift-Adaptive MLOps
- **Evidently AI** — data drift and model performance monitoring
- **Apache Airflow** — automated retraining DAG
- **GitHub Actions** — CI/CD with evaluation gates
- Target: recover to within 5% of pre-drift F1 within 48 hours of drift detection

### Phase 5 (Week 8) — Deployment and Evaluation
- **FastAPI** — extended serving for model predictions and agent outputs
- **Docker + Kubernetes** — full container orchestration
- **Grafana + Prometheus** — observability and alerting
- Structured 20-case expert evaluation study for RQ3

---

## 10. Build Status

| Component | Status |
|-----------|--------|
| Raspberry Pi sensor scripts (all 5 sensors) | ✅ Production |
| Firebase Firestore data collection | ✅ Production |
| Firebase → PostgreSQL ingestion worker | ✅ Production |
| PostgreSQL + TimescaleDB schemas | ✅ Complete |
| FastAPI backend with 5 endpoints | ✅ Production |
| Redis caching layer | ✅ Production |
| Pydantic data models (14 parameters) | ✅ Complete |
| Docker + Render deployment | ✅ Production |
| Next.js dashboard | ✅ Production |
| 70,000+ real sensor records | ✅ Live |
| Rule-based anomaly baseline (40% false-alert reduction) | ✅ Operational |
| Kafka + Spark streaming layer (`research_extension/phase1_streaming/`) | ✅ Built (Week 1–2) |
| Benchmark dataset (labeled, train/val/test split) | ✅ Built — 8 real stations, scoped to temperature/humidity/weight/power (`research_extension/phase2_models/`) |
| LSTM + Isolation Forest + classifier + two-stage models | ✅ Built — LSTM best, attribution F1=0.415, well below RQ1's 0.80 target (see PENDING_TASKS.md Part C for the full 14-round history) |
| LangGraph multi-agent system (`research_extension/phase3_agents/`) | ✅ Built — SensorDriftAgent, ThresholdBreachAgent (placeholder thresholds, not real EPA data), IncidentReportAgent (RAG-grounded Claude call), StakeholderEscalationAgent (simulated routing, no real notifications sent). Needs `ANTHROPIC_API_KEY` to actually run the LLM step. |
| Evidently + Airflow MLOps pipeline | 🔲 Week 6–7 |
| Kubernetes + Grafana deployment | 🔲 Week 8 |

---

## 11. Environment Variables Required

### Backend (`awh_az/backend/.env`)
```
DATABASE_URL=postgresql://user:password@host:5432/awh_db
REDIS_URL=redis://localhost:6379
FIREBASE_CREDENTIALS_PATH=/path/to/firebase-key.json
GOOGLE_CLOUD_PROJECT=your-project-id
```

### Ingestion Worker (`.env` at root)
```
DATABASE_URL=postgresql://user:password@host:5432/awh_db
FIREBASE_CREDENTIALS_PATH=/path/to/firebase-key.json
CHECKPOINT_PATH=/var/lib/awh-ingestion/checkpoint.json
POLL_INTERVAL_SECONDS=60
BATCH_SIZE=500
MAX_RETRIES=5
```

---

## 12. How To Run Locally

### Backend API
```bash
cd awh_az/backend
pip install -r requirements.txt
cp .env.example .env   # fill in your values
uvicorn main:app --reload --port 8000
```

### Ingestion Worker
```bash
pip install -r requirements_ingestion.txt
python ingestion_worker.py
```

### Dashboard
```bash
cd az_awh_dashboard
npm install
npm run dev            # runs on localhost:3000
```

### Raspberry Pi Station
```bash
cd RPi_USB_Package
python AquaPars1.py    # full station runtime
# or test individual sensors (flat layout, no test_system/ subfolder):
python test_balance.py
python test_intake_anememoter.py
python test_outtaketake_anememoter.py
python test_flow.py
```

---

## 13. Key Design Decisions and Why

| Decision | Rationale |
|----------|-----------|
| Firebase as edge store → PostgreSQL as analytics store | Firebase gives the Pi a reliable cloud write target with no schema enforcement; PostgreSQL gives the backend efficient range queries and relational integrity |
| `ON CONFLICT DO NOTHING` in ingestion | Double idempotency: checkpoint prevents re-fetching; conflict clause prevents duplicates if checkpoint is stale after a crash |
| Atomic checkpoint write (`.tmp` → rename) | If process is killed mid-write, original checkpoint is intact — prevents re-ingesting all historical data on restart |
| All 14 sensor fields Optional in Pydantic | Different stations have different hardware configurations — schema tolerates heterogeneous setups without failing |
| TimescaleDB schema alongside simple PostgreSQL | Hypertables give 10–100× faster range queries at scale; simple schema for dev/low-volume deployments |
| Redis cache in front of API | Dashboards repeatedly request the same date ranges — caching reduces DB load and latency for common read patterns |
| udev rules for stable USB port names | Linux assigns ttyUSB numbers by plug-in order at boot — udev rules bind each sensor to a permanent symlink regardless of plug order |
| Time-based train/val/test split (not random) | Random splits on time-series data leak future information into training, producing falsely optimistic model evaluation |

---

## 14. Glossary

| Term | Definition |
|------|-----------|
| AWH | Atmospheric Water Harvesting — extracting liquid water from humid air |
| SSEBE | School of Sustainable Engineering and the Built Environment, ASU |
| AquaPars | Name of the station hardware unit and runtime orchestrator |
| Harvesting Efficiency | Fraction of available atmospheric moisture captured as liquid water (computed metric, see `guides/HARVESTING_EFFICIENCY_FORMULA.md`) |
| cp210x | The USB-to-serial converter chip inside the anemometer and balance USB cables |
| ttyUSB | Linux device name for USB serial ports |
| Hypertable | TimescaleDB's time-partitioned table structure for efficient time-series queries |
| Co-anomaly | Anomaly manifesting simultaneously across multiple sensor parameters — indicates a real physical event rather than noise |
| Calibration drift | Slow progressive shift in a sensor's output away from its true value under sustained field conditions |
| RAG | Retrieval-Augmented Generation — LLM generation grounded in retrieved historical sensor data and regulatory documents |
| LangGraph | Python library for building stateful multi-agent AI systems as directed graphs |
| Evidently AI | ML observability tool for monitoring data drift and model performance degradation |

---

*Last updated: May 2026. Reflects end of operational build phase and start of
research extension (Summer 2026).*
