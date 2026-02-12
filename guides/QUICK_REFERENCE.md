# Ingestion Worker Quick Reference

## One-Liner Setup

```bash
# 1. Database
psql -h localhost -U postgres -d awh_db < schema_timescaledb.sql

# 2. Dependencies
pip install -r requirements_ingestion.txt

# 3. Environment
export DATABASE_URL="postgresql://user:pass@localhost/awh_db"
export FIREBASE_CREDENTIALS_PATH="./firebase-key.json"

# 4. Run
python ingestion_worker.py
```

## Key Classes

```python
CheckpointManager
├─ load()           # Load last timestamp
├─ save(ts, count)  # Atomically update progress

FirebaseClient
├─ fetch_new_documents(since_ts, limit)  # Query Firestore

StationManager
├─ get_or_create(name, cursor)  # Auto-create & cache stations

MeasurementInserter
├─ insert_batch(docs)  # Bulk insert with idempotency

IngestionWorker
├─ initialize()    # Connect to DB & Firebase
├─ run_once(ts)    # Single polling cycle
└─ main_loop()     # Infinite polling loop
```

## Core Logic

```python
# Main loop (simplified)
while True:
    checkpoint = load_checkpoint()
    docs = fetch_from_firebase(since=checkpoint)
    
    if docs:
        for doc in docs:
            station_id = get_or_create_station(doc['station_name'])
            insert_measurement(doc, station_id)
        
        update_checkpoint(max(timestamps))
    
    sleep(60)
```

## Idempotency

**SQL constraint prevents duplicates:**
```sql
ON CONFLICT (time, station_id) DO NOTHING
```

**Python guarantee:**
```python
# Same batch processed twice → second insert ignored
# Worker crash → restart from checkpoint → reprocess batch safely
```

## Error Scenarios

| Scenario | Behavior |
|----------|----------|
| Firebase down | Backoff, retry from same checkpoint |
| PostgreSQL down | Backoff, retry from same checkpoint |
| Malformed doc | Log warning, skip, continue |
| Checkpoint corrupted | Fallback to epoch (full reprocess) |
| Crash mid-batch | Restart, reprocess batch (idempotent) |

## Environment Variables

```bash
# Required
DATABASE_URL=postgresql://user:pass@host:5432/db
FIREBASE_CREDENTIALS_PATH=/path/to/key.json

# Optional (with defaults)
CHECKPOINT_PATH=/var/lib/awh-ingestion/checkpoint.json
POLL_INTERVAL_SECONDS=60
BATCH_SIZE=500
MAX_RETRIES=5
```

## Database Schema (Minimal)

```sql
-- Stations
CREATE TABLE stations (
    station_id SERIAL PRIMARY KEY,
    station_name TEXT UNIQUE NOT NULL,
    location TEXT,
    created_at TIMESTAMPTZ
);

-- Measurements (TimescaleDB hypertable)
CREATE TABLE measurements (
    time TIMESTAMPTZ NOT NULL,
    station_id INT REFERENCES stations,
    temperature DOUBLE PRECISION,
    humidity DOUBLE PRECISION,
    velocity DOUBLE PRECISION,
    unit TEXT,
    -- ... more fields
    UNIQUE (time, station_id)  -- Idempotency key
);

SELECT create_hypertable('measurements', 'time');
```

## Firebase JSON Format

```json
{
  "station_name": "Station A",
  "timestamp": "2025-10-02T13:15:53.937Z",
  "temperature": 27.5,
  "humidity": 65,
  "velocity": 0.19,
  "unit": "m/s",
  "weight": 1387.5,
  "pump_status": 0,
  "voltage": 115.9,
  "power": 921.9,
  "energy": 64703,
  "flow_lmin": 0,
  "flow_hz": 0,
  "flow_total": 0
}
```

## Systemd Service (Production)

```ini
[Unit]
Description=AWH Ingestion Worker
After=network.target postgresql.service

[Service]
Type=simple
User=awh-ingestion
WorkingDirectory=/opt/awh-ingestion
EnvironmentFile=/opt/awh-ingestion/.env
ExecStart=/usr/bin/python3 /opt/awh-ingestion/ingestion_worker.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Start with:
```bash
sudo systemctl enable awh-ingestion
sudo systemctl start awh-ingestion
sudo journalctl -u awh-ingestion -f
```

## Monitoring Queries

```sql
-- Latest data
SELECT MAX(time) FROM measurements;

-- Docs per hour (last 24h)
SELECT DATE_TRUNC('hour', time), COUNT(*)
FROM measurements
WHERE time > NOW() - INTERVAL '24 hours'
GROUP BY 1 ORDER BY 1 DESC;

-- By station
SELECT s.station_name, COUNT(*)
FROM measurements m
JOIN stations s ON m.station_id = s.station_id
WHERE m.time > NOW() - INTERVAL '7 days'
GROUP BY 1 ORDER BY 2 DESC;
```

## Performance Targets

| Metric | Target | Notes |
|--------|--------|-------|
| Latency | < 5 min | Time from edge upload to DB |
| Throughput | 30K docs/hr | 500 batch × 60 cycles/hr |
| Reliability | 99.9% | Auto-restart, idempotent |
| CPU | < 10% | Single process, idle most of time |
| Memory | < 200MB | Station cache + buffers |

## Failure Recovery

```bash
# Worker crashed
systemctl status awh-ingestion  # Check status
systemctl start awh-ingestion    # Restart
# Worker automatically resumes from checkpoint

# Checkpoint corrupted
rm /var/lib/awh-ingestion/checkpoint.json
systemctl restart awh-ingestion
# Starts from epoch (will reprocess all)

# Need to reset to specific time
echo '{"last_processed_timestamp": "2025-10-02T12:00:00.000Z"}' \
  > /var/lib/awh-ingestion/checkpoint.json
systemctl restart awh-ingestion
```

## Scaling

### To 100K+ docs/hour

1. **Batch size:** 5000 → 10000
2. **Use COPY:** Replace INSERT with COPY FROM STDIN (10x faster)
3. **Multi-worker:** Shard by station (station_id % 4 = worker_id)
4. **Connection pooling:** PgBouncer (25 → 100 connections)
5. **TimescaleDB tuning:** Compression, chunking, indexes

### Add Redis Cache

```python
redis = Redis()

def get_or_create_station(name):
    cached = redis.get(f"station:{name}")
    if cached:
        return int(cached)
    
    station_id = db_lookup(name)
    redis.set(f"station:{name}", station_id)
    return station_id
```

### Add ML Feature Pipeline

```sql
-- Query raw data
SELECT temperature, humidity, velocity FROM measurements
WHERE station_id = 3 AND time > NOW() - INTERVAL '30 days'
ORDER BY time;

-- Write predictions
INSERT INTO predictions (time, station_id, predicted_yield)
VALUES (...);
```

## Testing

```bash
# Unit tests (fast, no DB required)
pytest test_ingestion_worker.py -v

# Integration test (requires real DB)
pytest test_integration.py -v

# Manual test
python -c "
from ingestion_worker import FirebaseClient
client = FirebaseClient('./firebase-key.json')
docs = client.fetch_new_documents('2025-10-01T00:00:00.000Z', limit=5)
print(f'Fetched {len(docs)} documents')
"
```

## Log Format

```
2025-10-02 13:16:02 [INFO] Firebase initialized
2025-10-02 13:16:03 [INFO] Connected to PostgreSQL
2025-10-02 13:16:03 [INFO] Loaded 2 stations into cache
2025-10-02 13:16:03 [INFO] Loaded checkpoint: 2025-10-02T12:00:00.000Z (45627 docs)
2025-10-02 13:16:04 [INFO] Fetched 103 documents from Firebase
2025-10-02 13:16:04 [INFO] Inserted 103 measurements (total: 45730)
2025-10-02 13:17:04 [DEBUG] No new documents in this cycle
2025-10-02 13:17:04 [DEBUG] Sleeping for 60s
```

## Files at a Glance

```
ingestion_worker.py         Main worker (15 KB)
requirements_ingestion.txt  Dependencies (3 lines)
schema_timescaledb.sql      Database setup
test_ingestion_worker.py    Unit tests
INGESTION_README.md         Full documentation
DEPLOYMENT_GUIDE.md         Production setup
QUICK_REFERENCE.md          This file
```

## Support Checklist

- [ ] Database is up: `psql -l`
- [ ] Schema is created: `psql -c "\dt measurements"`
- [ ] Firebase key is valid: `cat firebase-key.json | jq .`
- [ ] Firestore has documents: (check in Console)
- [ ] Worker runs: `python ingestion_worker.py`
- [ ] Logs show progress: `tail -f /var/log/awh-ingestion.log`
- [ ] Data appears: `SELECT COUNT(*) FROM measurements;`
