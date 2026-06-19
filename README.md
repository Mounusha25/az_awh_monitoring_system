# AzAWH — Arizona Atmospheric Water Harvesting Monitoring System

Full-stack monitoring platform for AWH stations deployed at ASU (SSEBE lab).
Collects sensor data from physical hardware, stores it in PostgreSQL, and serves it via a REST API and dashboard.

**Status:** Production — 1.4M+ sensor records across 8 active stations.

---

## Where Things Run

```
az_awh_monitoring_system/
│
├── RPi_USB_Package/        → runs ON each Raspberry Pi station
├── awh_az/backend/         → FastAPI server, deployed on Render
├── ingestion_worker.py     → runs on a server, moves Firestore → PostgreSQL
├── Code_Original/          → historical reference only (do not deploy)
└── guides/                 → documentation
```

| Component | Runs Where | What It Does |
|-----------|-----------|--------------|
| `RPi_USB_Package/` | Raspberry Pi (each station) | Reads sensors, writes to Firebase |
| `awh_az/backend/` | Render.com (cloud) | FastAPI REST API over PostgreSQL |
| `ingestion_worker.py` | Any server / cron job | Polls Firestore, inserts into PostgreSQL |
| `az_awh_dashboard` (separate repo) | Vercel (cloud) | Next.js dashboard, reads the API |

---

## Quick Start

### Raspberry Pi Station
```bash
cd RPi_USB_Package
python AquaPars1.py
```

### Backend API
```bash
cd awh_az/backend
pip install -r requirements.txt
cp .env.example .env    # fill in DATABASE_URL, REDIS_URL, FIREBASE_CREDENTIALS_PATH
uvicorn main:app --reload --port 8000
```

### Ingestion Worker
```bash
pip install -r requirements_ingestion.txt
python ingestion_worker.py
```

---

## Root-Level Files

| File | Purpose |
|------|---------|
| `ingestion_worker.py` | Firebase → PostgreSQL ETL pipeline |
| `requirements_ingestion.txt` | Dependencies for the ingestion worker |
| `schema_postgresql_simple.sql` | PostgreSQL schema (no TimescaleDB) |
| `schema_timescaledb.sql` | PostgreSQL + TimescaleDB hypertable schema |
| `test_ingestion_worker.py` | Integration tests for the ingestion worker |
| `verify_setup.py` | Sanity-check that env vars and connections work |
| `prepare_for_rpi.sh` | Packages `RPi_USB_Package/` for SCP transfer to a Pi |
| `read_env_anemometer.py` | Standalone anemometer diagnostic (not part of main loop) |

---

## Data Flow

```
[Raspberry Pi]
    sensors → AquaPars1.py → Firebase Firestore
                                     ↓
                          ingestion_worker.py
                                     ↓
                            PostgreSQL / TimescaleDB
                                     ↓
                          awh_az/backend (FastAPI)
                                     ↓
                          az_awh_dashboard (Next.js)
```

---

## Documentation

All guides are in [`guides/`](guides/):

- [`ARCHITECTURE.md`](guides/ARCHITECTURE.md) — system architecture overview
- [`INGESTION_README.md`](guides/INGESTION_README.md) — ingestion worker setup and operation
- [`DEPLOYMENT_GUIDE.md`](guides/DEPLOYMENT_GUIDE.md) — deploying backend and worker to production
- [`UI_GUIDE.md`](guides/UI_GUIDE.md) — Raspberry Pi touchscreen UI
- [`HARVESTING_EFFICIENCY_FORMULA.md`](guides/HARVESTING_EFFICIENCY_FORMULA.md) — efficiency metric calculation
- [`QUICK_REFERENCE.md`](guides/QUICK_REFERENCE.md) — common commands and shortcuts

For full project context (architecture decisions, research objectives, data model), see [`CLAUDE.md`](CLAUDE.md).

---

## Tech Stack

| Layer | Stack |
|-------|-------|
| Edge / Station | Python, pyserial, gpiozero, Firebase Admin SDK |
| Ingestion | Python, psycopg2, firebase-admin |
| Backend API | FastAPI, Pydantic v2, Redis, PostgreSQL/TimescaleDB |
| Dashboard | Next.js 15, React 19, TypeScript, Tailwind CSS |
| Deployment | Docker, Render.com (API), Vercel (dashboard) |

---

*SSEBE Lab, Arizona State University — Mounusha Ram Metti (mmetti@asu.edu)*
