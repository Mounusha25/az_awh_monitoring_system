# 📋 AWH SYSTEM - DOCUMENTATION INDEX

**Welcome to the AWH (Atmospheric Water Harvesting) Monitoring System Implementation**

This index helps you navigate all project documentation and code.

---

## 🚀 Getting Started

**New to the project?** Start here:

1. **[STATUS_REPORT.md](STATUS_REPORT.md)** - System overview and readiness status
2. **[INGESTION_README.md](INGESTION_README.md)** - Cloud ingestion user guide
3. **[UI_GUIDE.md](UI_GUIDE.md)** - Tkinter control panel guide

---

## 📚 Documentation by Purpose

### For Developers
- **[INGESTION_README.md](INGESTION_README.md)** - User guide and architecture
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design with diagrams
- **[UI_GUIDE.md](UI_GUIDE.md)** - Full UI usage guide
- **[UI_QUICKREF.md](UI_QUICKREF.md)** - UI quick reference card

### For Operations
- **[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)** - Production setup and deployment
- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - Commands and operational procedures

### For Troubleshooting
- **[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)** - Section 5: Troubleshooting (10+ issues)
- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - Support checklist
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - Error handling flowchart

---

## 📁 File Structure

### Root-Level Files (Cloud Ingestion)
```
📄 ingestion_worker.py       — Firebase → PostgreSQL ingestion worker
📄 test_ingestion_worker.py  — Test suite for ingestion worker
📄 verify_setup.py           — Setup verification script
📄 read_env_anemometer.py    — Environment anemometer reader
📄 prepare_for_rpi.sh        — RPi setup script
📄 requirements_ingestion.txt— Python dependencies
📄 schema_timescaledb.sql    — TimescaleDB schema
📄 schema_postgresql_simple.sql — Simple PostgreSQL schema
```

### RPi_USB_Package/ (Edge Device — runs on Raspberry Pi)
```
📄 AquaPars1.py              — Main controller (run this on the RPi)
📄 awh_ui_layout.py          — Tkinter control panel UI
📄 pump_controller.py        — GPIO pump control (gpiozero, RPi 4 & 5)
📄 read_balance.py           — Serial balance reader (auto-detect)
📄 read_power.py             — Modbus power meter reader
📄 read_flow.py              — Pulse flow meter reader (gpiozero)
📄 intake_anemometer.py      — Intake anemometer decoder
📄 outtake_anemometer.py     — Outtake anemometer decoder
📄 sim_run_on_mac.py         — Mac simulation (no hardware needed)
📄 test_system/              — Hardware test scripts
```

### Documentation (guides/)
```
📘 INDEX.md                  — This file (navigation guide)
📘 STATUS_REPORT.md          — System readiness assessment
📘 INGESTION_README.md       — Cloud ingestion user guide
📘 DEPLOYMENT_GUIDE.md       — Production deployment
📘 QUICK_REFERENCE.md        — Operational commands
📘 ARCHITECTURE.md           — System design & diagrams
📘 UI_GUIDE.md               — UI usage guide
📘 UI_QUICKREF.md            — UI quick reference card
```

---

## 🎯 Quick Navigation by Task

### I need to understand the system
1. Read: [STATUS_REPORT.md](STATUS_REPORT.md) (5 min)
2. Read: [INGESTION_README.md](INGESTION_README.md) (10 min)
3. Study: [ARCHITECTURE.md](ARCHITECTURE.md) (20 min)

### I need to deploy to production
1. Read: [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) (20 min)
2. Follow: Systemd or Cloud Run section (depends on platform)
3. Reference: [QUICK_REFERENCE.md](QUICK_REFERENCE.md) (as needed)

### I need to troubleshoot an issue
1. Check: [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) section 5 (Troubleshooting)
2. Reference: [QUICK_REFERENCE.md](QUICK_REFERENCE.md) (support checklist)
3. Study: [ARCHITECTURE.md](ARCHITECTURE.md) (error handling flowchart)

### I need to understand the code
1. Start: [ingestion_worker.py](ingestion_worker.py) (read from top)
2. Study: Test file [test_ingestion_worker.py](test_ingestion_worker.py) (examples)
3. Reference: [ARCHITECTURE.md](ARCHITECTURE.md) (design rationale)

### I need to monitor the system
1. Run: Monitoring queries from [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
2. Check: Logs (structured JSON output)
3. Verify: Checkpoint file at `/var/lib/awh-ingestion/checkpoint.json`
4. Review: Performance benchmarks in [QUICK_REFERENCE.md](QUICK_REFERENCE.md)

### I need to set up the database
1. Follow: [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) section 1 (Database Setup)
2. Execute: `psql < schema_timescaledb.sql`
3. Reference: [schema_timescaledb.sql](schema_timescaledb.sql) (schema details)

### I need to configure the system
1. Review: Environment variables in [INGESTION_README.md](INGESTION_README.md) section 4
2. Create: `.env` file with required variables
3. Verify: `python -c "import dotenv; dotenv.load_dotenv(); ..."`

---

## 📊 Document Reference

| Document | Purpose | Length | Time to Read |
|----------|---------|--------|-------------|
| [STATUS_REPORT.md](STATUS_REPORT.md) | System overview | Comprehensive | 10 min |
| [INGESTION_README.md](INGESTION_README.md) | User guide | 8.7 KB | 15 min |
| [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) | Deployment | 7.0 KB | 15 min |
| [QUICK_REFERENCE.md](QUICK_REFERENCE.md) | Operations | 4.0 KB | 10 min |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Design | 519 lines | 20 min |
| [UI_GUIDE.md](UI_GUIDE.md) | UI usage | Comprehensive | 10 min |
| [UI_QUICKREF.md](UI_QUICKREF.md) | UI reference | Quick | 5 min |

---

## 🔍 Key Concepts

### Checkpointing
Learn about: [ARCHITECTURE.md](ARCHITECTURE.md) section "Checkpoint State Machine"
Implementation: [ingestion_worker.py](ingestion_worker.py) lines 1-80
Tests: [test_ingestion_worker.py](test_ingestion_worker.py) lines 1-80

### Idempotency
Learn about: [ARCHITECTURE.md](ARCHITECTURE.md) section "Idempotency Guarantee"
Implementation: [schema_timescaledb.sql](schema_timescaledb.sql) UNIQUE constraint
Tests: [test_ingestion_worker.py](test_ingestion_worker.py) lines 222-283

### Station Caching
Learn about: [ARCHITECTURE.md](ARCHITECTURE.md) section "Station Cache Evolution"
Implementation: [ingestion_worker.py](ingestion_worker.py) lines 132-200
Tests: [test_ingestion_worker.py](test_ingestion_worker.py) lines 100-140

### Error Handling
Learn about: [ARCHITECTURE.md](ARCHITECTURE.md) section "Error Handling Flowchart"
Implementation: [ingestion_worker.py](ingestion_worker.py) lines 281-350
Tests: [test_ingestion_worker.py](test_ingestion_worker.py) lines 182-220

### Scaling Strategy
Learn about: [ARCHITECTURE.md](ARCHITECTURE.md) section "Scaling Strategy"
Current: Single worker (v1)
Planned: Multi-worker (v2), Kafka (v3), ML pipeline (v4)

---

## 🚀 Quick Start Workflow

### Step 1: Preparation (15 min)
- [ ] Read [STATUS_REPORT.md](STATUS_REPORT.md)
- [ ] Read [UI_GUIDE.md](UI_GUIDE.md)
- [ ] Understand requirements from [INGESTION_README.md](INGESTION_README.md)

### Step 2: Setup (1-2 hours)
- [ ] Install: `pip install -r requirements_ingestion.txt`
- [ ] Database: `psql < schema_timescaledb.sql`
- [ ] Configure: Create `.env` file
- [ ] Test: `pytest test_ingestion_worker.py -v`

### Step 3: Verify (30 min)
- [ ] Check database: `psql -c "SELECT * FROM stations;"`
- [ ] Test Firebase connection
- [ ] Run single polling cycle
- [ ] Verify checkpoint file created

### Step 4: Deploy (1-3 hours)
- [ ] Choose platform (Systemd or Cloud Run)
- [ ] Follow [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)
- [ ] Monitor with queries from [QUICK_REFERENCE.md](QUICK_REFERENCE.md)

---

## 📞 Support Resources

### Documentation
- **How to**: [INGESTION_README.md](INGESTION_README.md) (user guide)
- **Deploy**: [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) (2 options)
- **Commands**: [QUICK_REFERENCE.md](QUICK_REFERENCE.md) (reference)
- **Design**: [ARCHITECTURE.md](ARCHITECTURE.md) (system design)

### Code
- **Implementation**: [ingestion_worker.py](ingestion_worker.py) (60+ comments)
- **Schema**: [schema_timescaledb.sql](schema_timescaledb.sql) (20+ comments)
- **Tests**: [test_ingestion_worker.py](test_ingestion_worker.py) (examples)

### Monitoring
- **Logs**: Structured JSON output (see [INGESTION_README.md](INGESTION_README.md))
- **Checkpoint**: Observable filesystem state
- **Queries**: SQL monitoring queries in [QUICK_REFERENCE.md](QUICK_REFERENCE.md)

---

## ✅ Verification Checklist

Before deploying, verify:

- [ ] Read all documentation in this index
- [ ] Understand system architecture (see [ARCHITECTURE.md](ARCHITECTURE.md))
- [ ] Review code implementation ([ingestion_worker.py](ingestion_worker.py))
- [ ] Run test suite: `pytest test_ingestion_worker.py -v`
- [ ] Set up local PostgreSQL + TimescaleDB
- [ ] Create `.env` configuration file
- [ ] Test Firebase connection
- [ ] Run single polling cycle manually
- [ ] Choose deployment platform (Systemd or Cloud Run)
- [ ] Follow deployment procedures (see [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md))
- [ ] Configure monitoring (see [QUICK_REFERENCE.md](QUICK_REFERENCE.md))
- [ ] Set up backup strategy

---

## 🎓 Learning Path

**Recommended reading order for understanding:**

1. **Day 1: Overview** (30 min)
   - [PROJECT_DASHBOARD.md](PROJECT_DASHBOARD.md) - Visual overview
   - [MANIFEST.md](MANIFEST.md) - Project scope

2. **Day 1: Architecture** (1 hour)
   - [ARCHITECTURE.md](ARCHITECTURE.md) - System design
   - [INGESTION_README.md](INGESTION_README.md) - Features

3. **Day 2: Implementation** (2 hours)
   - [ingestion_worker.py](ingestion_worker.py) - Read code
   - [test_ingestion_worker.py](test_ingestion_worker.py) - Study tests
   - [schema_timescaledb.sql](schema_timescaledb.sql) - Understand schema

4. **Day 3: Deployment** (1 hour)
   - [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) - Setup procedures
   - [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - Operational reference

5. **Day 4: Operations** (1 hour)
   - Configure environment
   - Run local tests
   - Set up database
   - Deploy and monitor

---

## 📄 Document Map

```
START HERE
    ↓
PROJECT_DASHBOARD.md (visual overview)
    ├─ For Project Managers
    │  └─ MANIFEST.md (inventory)
    │  └─ IMPLEMENTATION_SUMMARY.txt (overview)
    │
    ├─ For Developers
    │  ├─ ARCHITECTURE.md (design)
    │  ├─ INGESTION_README.md (user guide)
    │  ├─ ingestion_worker.py (code)
    │  └─ test_ingestion_worker.py (tests)
    │
    └─ For Operations
       ├─ DEPLOYMENT_GUIDE.md (setup)
       ├─ QUICK_REFERENCE.md (commands)
       └─ schema_timescaledb.sql (schema)
```

---

## 🎯 Final Notes

- **All documentation is current** as of February 9, 2026
- **All code is production-ready** with full test coverage
- **All procedures are tested** and verified working
- **All deployments are documented** (Systemd + Cloud Run)
- **All support resources included** (troubleshooting, monitoring, scaling)

---

**Status**: ✅ PRODUCTION-READY

**Next Action**: Choose your starting point from the navigation links above!

---

*AWH (Atmospheric Water Harvesting) Monitoring System*  
*Documentation Index*  
*February 9, 2026*
