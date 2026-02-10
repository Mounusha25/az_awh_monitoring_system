# Ingestion Worker Architecture Document

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    EDGE DEVICES (Raspberry Pi)              │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐         │
│  │ Anemometer 1 │ │ Anemometer 2 │ │ Balance      │ ...     │
│  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘         │
│         │                 │                 │                │
│         └─────────────────┼─────────────────┘                │
│                           │                                  │
│                      ┌────v────┐                             │
│                      │ CSV Log │ (Local backup)             │
│                      └────┬────┘                             │
│                           │                                  │
│               Upload JSON every 60-180 seconds              │
└───────────────────────────┼───────────────────────────────────┘
                            │
                            ▼
        ┌────────────────────────────────────────┐
        │        FIREBASE (Firestore)            │
        │  ┌──────────────────────────────────┐  │
        │  │ Measurements Collection          │  │
        │  │ {station, timestamp, sensors...} │  │
        │  └──────────────────────────────────┘  │
        │                                        │
        │     (Ingress buffer + decoupling)     │
        └────────────────────────────────────────┘
                            │
                 Polling every 60s
                 (With checkpointing)
                            │
                            ▼
        ┌────────────────────────────────────────┐
        │   INGESTION WORKER (Python)            │
        │                                        │
        │  ┌──────────────────────────────────┐  │
        │  │ FirebaseClient                   │  │
        │  │ • Query by timestamp             │  │
        │  │ • Batch fetch (500 docs/cycle)  │  │
        │  └──────────────────────────────────┘  │
        │                                        │
        │  ┌──────────────────────────────────┐  │
        │  │ CheckpointManager                │  │
        │  │ • Track last processed timestamp │  │
        │  │ • Atomic checkpoint writes       │  │
        │  │ • Survive crashes               │  │
        │  └──────────────────────────────────┘  │
        │                                        │
        │  ┌──────────────────────────────────┐  │
        │  │ StationManager                   │  │
        │  │ • In-memory station cache        │  │
        │  │ • Auto-create missing stations   │  │
        │  │ • Map station_name → station_id  │  │
        │  └──────────────────────────────────┘  │
        │                                        │
        │  ┌──────────────────────────────────┐  │
        │  │ MeasurementInserter              │  │
        │  │ • Bulk batch insert (500 rows)   │  │
        │  │ • Idempotent (ON CONFLICT)       │  │
        │  │ • Atomic transaction             │  │
        │  └──────────────────────────────────┘  │
        │                                        │
        │  ┌──────────────────────────────────┐  │
        │  │ Retry Logic                      │  │
        │  │ • Exponential backoff            │  │
        │  │ • 1s, 2s, 4s, 8s, 16s delays   │  │
        │  │ • Up to 5 retries               │  │
        │  └──────────────────────────────────┘  │
        └────────────────────────────────────────┘
                            │
                  INSERT with idempotency
                  ON CONFLICT DO NOTHING
                            │
                            ▼
        ┌────────────────────────────────────────┐
        │   PostgreSQL + TimescaleDB             │
        │                                        │
        │  ┌──────────────────────────────────┐  │
        │  │ stations table                   │  │
        │  │ • station_id (PK)               │  │
        │  │ • station_name (UNIQUE)         │  │
        │  │ • location                      │  │
        │  │ • created_at                    │  │
        │  └──────────────────────────────────┘  │
        │                                        │
        │  ┌──────────────────────────────────┐  │
        │  │ measurements hypertable          │  │
        │  │ • time (PK)                     │  │
        │  │ • station_id (FK)               │  │
        │  │ • temperature, humidity, etc.    │  │
        │  │ • UNIQUE (time, station_id)     │  │
        │  │ • Compressed after 30 days      │  │
        │  └──────────────────────────────────┘  │
        │                                        │
        │   (Single source of truth for analytics)
        └────────────────────────────────────────┘
                            │
                            ▼
        ┌────────────────────────────────────────┐
        │         ANALYTICS & DASHBOARDS         │
        │                                        │
        │  • SQL queries on raw data             │
        │  • Hourly/daily aggregations           │
        │  • ML feature engineering              │
        │  • Web dashboard (React/Dash)          │
        └────────────────────────────────────────┘
```

---

## Data Flow (One Polling Cycle)

```
START
  │
  ├─ load_checkpoint()
  │  └─ Read: last_processed_timestamp = "2025-10-02T13:15:53.937Z"
  │
  ├─ fetch_new_documents(since_timestamp)
  │  │
  │  └─ Firebase Query:
  │     WHERE timestamp > "2025-10-02T13:15:53.937Z"
  │     ORDER BY timestamp
  │     LIMIT 500
  │     ▼
  │  Result: 103 documents
  │
  ├─ for each document:
  │  │
  │  ├─ get_or_create_station(doc['station_name'])
  │  │  │
  │  │  ├─ Check in-memory cache → Hit or Miss
  │  │  │
  │  │  ├─ If Miss: Query DB "SELECT station_id FROM stations WHERE ..."
  │  │  │
  │  │  └─ If Not Found: INSERT new station, cache result
  │  │     └─ station_id = 3
  │  │
  │  ├─ Extract fields:
  │  │  └─ (time, station_id, temp, humidity, velocity, ...)
  │  │
  │  └─ Add row to batch
  │     [Row 1: (2025-10-02T13:15:53.937Z, 3, 27.5, 65, 0.19, ...)]
  │     [Row 2: (2025-10-02T13:16:01.102Z, 1, 28.1, 62, 0.21, ...)]
  │     [Row 3: ...]
  │     ...
  │
  ├─ BEGIN TRANSACTION
  │
  ├─ INSERT all rows (batch)
  │  │
  │  └─ INSERT INTO measurements (...) VALUES (...)
  │     ON CONFLICT (time, station_id) DO NOTHING
  │     └─ Inserted 103 rows (or fewer if some were duplicates)
  │
  ├─ COMMIT TRANSACTION
  │
  ├─ update_checkpoint()
  │  │
  │  └─ Calculate: max_timestamp = "2025-10-02T13:16:53.102Z"
  │     
  │  └─ Write to file:
  │     {
  │       "last_processed_timestamp": "2025-10-02T13:16:53.102Z",
  │       "processed_count": 45730,
  │       "last_update": "2025-10-02T13:16:54.000Z"
  │     }
  │
  ├─ sleep(60 seconds)
  │
  └─ GOTO START
```

---

## Idempotency Guarantee

**Scenario:** Worker crashes, restarts, reprocesses same 103 documents

```
First Run:
  INSERT INTO measurements (time, station_id, ...)
  VALUES
    (2025-10-02T13:15:53.937Z, 3, ...),
    (2025-10-02T13:16:01.102Z, 1, ...),
    ...
  Result: 103 rows inserted, checkpoint updated

Crash → Restart

Second Run (same checkpoint, same documents):
  INSERT INTO measurements (time, station_id, ...)
  VALUES
    (2025-10-02T13:15:53.937Z, 3, ...),   ← CONFLICT
    (2025-10-02T13:16:01.102Z, 1, ...),   ← CONFLICT
    ...
  ON CONFLICT (time, station_id) DO NOTHING
  Result: 0 rows inserted (silent ignores duplicates), checkpoint unchanged

Perfect Idempotency!
```

**Why this works:**
- `time` is provided by edge device (synchronized via NTP)
- `station_id` is derived from `station_name` (deterministic)
- Combined `(time, station_id)` is globally unique per measurement
- Same data + same composite key = same row (conflicts are silently ignored)

---

## Error Handling Flow

```
┌─ Polling Cycle
│
├─ Try: fetch_from_firebase()
│  │
│  ├─ If SUCCESS: return docs
│  │
│  └─ If ERROR:
│     ├─ Attempt 1: wait 1s, retry
│     ├─ Attempt 2: wait 2s, retry
│     ├─ Attempt 3: wait 4s, retry
│     ├─ Attempt 4: wait 8s, retry
│     ├─ Attempt 5: wait 16s, retry
│     └─ If still failed: log error, continue with empty batch
│        (checkpoint doesn't update → will retry next cycle)
│
├─ Try: insert_to_postgres()
│  │
│  ├─ If SUCCESS: return row count, update checkpoint
│  │
│  └─ If ERROR (e.g., DB down):
│     ├─ ROLLBACK transaction
│     ├─ Exponential backoff retry (same as above)
│     └─ If still failed: checkpoint NOT updated
│        (will reprocess exact same batch next cycle = safe)
│
├─ Checkpoint update:
│  │
│  ├─ If SUCCESS: atomic write (temp + rename)
│  │  └─ Checkpoint now has new timestamp
│  │
│  └─ If ERROR (corrupted file):
│     └─ Next restart: fallback to epoch zero
│        (will reprocess all documents = data integrity maintained)
│
└─ Result: RESILIENT (no data loss, no duplicates)
```

---

## Checkpoint File State Machine

```
┌─ Startup
│  │
│  ├─ Try: load_checkpoint()
│  │
│  ├─ If EXISTS and VALID:
│  │  └─ Use last_processed_timestamp
│  │     Example: "2025-10-02T13:15:53.937Z"
│  │
│  ├─ If EXISTS but CORRUPTED:
│  │  └─ Fallback to epoch "1970-01-01T00:00:00.000Z"
│  │     (Logs warning, will reprocess all data)
│  │
│  └─ If NOT EXISTS:
│     └─ Start from epoch "1970-01-01T00:00:00.000Z"
│        (First-time setup, will ingest all historical data)
│
├─ Running
│  │
│  └─ For each polling cycle:
│     ├─ Query Firebase WHERE timestamp > checkpoint
│     ├─ Insert batch to DB
│     └─ If insert succeeds:
│        ├─ Calculate new_checkpoint = max(batch.timestamps)
│        └─ Atomically write checkpoint file
│           (Other processes can safely read it)
│
└─ Crash
   │
   └─ On restart:
      ├─ Load checkpoint (same logic as Startup)
      └─ Resume from there
         (May reprocess last batch if it was in flight)
         (Idempotent inserts handle this)
```

---

## Station Cache Evolution

```
Startup:
  ├─ Empty cache: {}
  │
  └─ Run: SELECT station_id, station_name FROM stations
     └─ Result: [(1, "Station A"), (2, "Station B"), (3, "Station C")]
     └─ Populate cache:
        {
          "Station A": 1,
          "Station B": 2,
          "Station C": 3
        }

Ingestion Cycle 1:
  ├─ Document has station_name = "Station A"
  ├─ Check cache: Hit! (station_id = 1)
  └─ No DB query needed

Ingestion Cycle 2:
  ├─ Document has station_name = "Station D" (new!)
  ├─ Check cache: Miss
  ├─ Query DB: SELECT station_id WHERE station_name = "Station D"
  ├─ Not found in DB
  ├─ INSERT into stations: INSERT INTO stations (station_name, ...) VALUES ("Station D", ...)
  │  └─ Returns station_id = 4
  ├─ Add to cache:
  │  {
  │    "Station A": 1,
  │    "Station B": 2,
  │    "Station C": 3,
  │    "Station D": 4    ← New
  │  }
  └─ Continue with station_id = 4

Performance Benefit:
  • Cycle 1: 1 DB query (SELECT *)
  • Cycle 2-N: 0 DB queries (100% cache hits)
  
  Assuming 10 new stations per hour:
  • Hour 1: 10 DB lookups
  • Hour 2-100: 0 DB lookups (all in cache)
  
  99% fewer DB queries after initial load!
```

---

## Scaling Strategy (Future)

### Current (Production v1)
```
Single Worker
└─ Polling every 60s
   └─ 500 docs/batch
      └─ 30K docs/hour throughput
```

### When Volume Increases (v2)
```
Multi-Worker Sharding
├─ Worker 1: Poll station_id IN (1, 4, 7, 10, ...)
├─ Worker 2: Poll station_id IN (2, 5, 8, 11, ...)
├─ Worker 3: Poll station_id IN (3, 6, 9, 12, ...)
└─ Results: 3x parallelism, 90K docs/hour
   └─ No coordination needed (stations are independent)
```

### When Data Volume Explodes (v3)
```
Distributed Ingestion
├─ Kafka Topic (from edge devices)
├─ Worker Pool (N workers, each consumes partition)
├─ COPY FROM STDIN (10x faster than INSERT)
├─ Redis Cache (station lookups to zero DB hits)
└─ Results: 1M+ docs/hour, sub-second latency
```

### ML Feature Pipeline (v4)
```
PostgreSQL (raw measurements)
    ↓ (Query every 10 min)
Feature Extractor
    ├─ Compute: temperature_diff, humidity_trend
    ├─ Aggregate: hourly_averages, daily_max
    └─ Write to: features table
    ↓
ML Model Training
    ├─ Load features from PostgreSQL
    ├─ Train prediction model
    └─ Store predictions back in DB
    ↓
Prediction Dashboard
```

---

## Key Design Principles

### 1. Idempotency First
- Database constraint (UNIQUE + ON CONFLICT)
- Safe to restart anytime
- No dedup logic in application

### 2. Checkpoint Everywhere
- Explicit state tracking
- Survives crashes
- Observable via file system

### 3. Fail-Safe Defaults
- Reprocess rather than lose data
- Exponential backoff (not aggressive retries)
- Detailed logging for debugging

### 4. Separation of Concerns
- Firebase = ingress buffer
- PostgreSQL = analytics source of truth
- Worker = glue layer

### 5. Incremental Scaling
- Start simple (single worker)
- No distributed locks/coordination
- Add workers/cache/replicas only when needed

---

## Files & Responsibilities

```
ingestion_worker.py
├─ CheckpointManager      (Persistence layer)
├─ FirebaseClient         (Firebase adapter)
├─ StationManager         (Station cache + DB ops)
├─ MeasurementInserter    (Batch insert logic)
├─ retry_with_backoff()   (Resilience)
├─ IngestionWorker        (Orchestration)
└─ main()                 (Entry point)

schema_timescaledb.sql
├─ Create stations table
├─ Create measurements hypertable
├─ Set up compression policy
└─ Set up retention policy

test_ingestion_worker.py
├─ CheckpointManager tests
├─ StationManager tests
├─ MeasurementInserter tests
├─ Retry logic tests
└─ Idempotency scenarios

DEPLOYMENT_GUIDE.md
├─ Systemd setup
├─ Cloud Run deployment
├─ Monitoring queries
└─ Troubleshooting

QUICK_REFERENCE.md
└─ One-liners & checklists
```

---

## Testing Strategy

```
Unit Tests (pytest)
├─ Checkpoint save/load
├─ Station cache behavior
├─ Retry logic with backoff
└─ Measurement batch construction

Integration Tests
├─ Real PostgreSQL connection
├─ Real Firebase query (mocked docs)
├─ Full ingestion cycle
└─ Idempotency verification

Manual Testing
├─ Monitor logs in real-time
├─ Check checkpoint file
├─ Query database directly
└─ Verify data matches Firebase
```

---

## Performance Benchmarks (Expected)

| Metric | Value | Notes |
|--------|-------|-------|
| **Latency** | 2-5 min | Edge upload → DB insert |
| **Throughput** | 30K docs/hour | 500 batch × 60 cycles/hr |
| **CPU Usage** | < 10% | Mostly idle (I/O bound) |
| **Memory** | 150-200 MB | Station cache + buffers |
| **Storage** | Grows with data | ~100 bytes/doc on SSD |
| **Availability** | 99.9% | Auto-restart on crash |
| **RTO** | < 1 min | Resume from checkpoint |
| **RPO** | 0 | No data loss (idempotent) |

---

## Success Criteria

✅ **Functional**
- Fetches all documents from Firebase
- Inserts into PostgreSQL without duplicates
- Survives crashes and network failures

✅ **Operational**
- Runs unattended 24/7
- Logs are informative and actionable
- Monitoring is straightforward

✅ **Scalable**
- Can be extended to multi-worker without rewrite
- No global locks or coordination required
- Database schema supports future growth

✅ **Reliable**
- Zero data loss
- Zero duplicate rows
- Deterministic error recovery
