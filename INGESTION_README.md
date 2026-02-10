# Firebase → PostgreSQL/TimescaleDB Ingestion Worker

Production-grade data pipeline for AWH (Atmospheric Water Harvesting) monitoring system.

Moves measurement data from Firebase (Firestore) to PostgreSQL/TimescaleDB with:
- ✅ **Idempotency** - Safe to restart, no duplicate rows
- ✅ **Checkpointing** - Survives crashes, continues from last checkpoint
- ✅ **Error Handling** - Graceful degradation, exponential backoff
- ✅ **Scalability** - Designed for 10K+ docs/hour with minimal changes
- ✅ **Station Management** - Auto-creates stations, caches lookups

## Architecture

```
Firebase (Firestore)
    ↓ [Polling]
Ingestion Worker
    ├─ Checkpoint (resume after crash)
    ├─ Station Cache (fast lookup)
    └─ Batch Insert (idempotent)
    ↓
PostgreSQL + TimescaleDB
    ├─ stations (station metadata)
    └─ measurements (time-series data)
```

## Quick Start

### 1. Database Setup

```bash
psql -h localhost -U postgres -d awh_db < schema_timescaledb.sql
```

Verifies:
- TimescaleDB extension installed
- `stations` table created
- `measurements` hypertable created with compression

### 2. Firebase Credentials

```bash
# Download from Firebase Console → Project Settings → Service Accounts
cp /path/to/firebase-key.json ./

# Restrict permissions
chmod 600 firebase-key.json
```

### 3. Install & Run

```bash
pip install -r requirements_ingestion.txt

export DATABASE_URL="postgresql://user:pass@localhost/awh_db"
export FIREBASE_CREDENTIALS_PATH="./firebase-key.json"

python ingestion_worker.py
```

Logs will show:
```
[INFO] Firebase initialized
[INFO] Connected to PostgreSQL
[INFO] Loaded 2 stations into cache
[INFO] Fetched 103 documents from Firebase
[INFO] Inserted 103 measurements (total: 45730)
```

## Configuration

Environment variables (or `.env` file):

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | (required) | PostgreSQL connection string |
| `FIREBASE_CREDENTIALS_PATH` | (required) | Path to Firebase service account key |
| `CHECKPOINT_PATH` | `/var/lib/awh-ingestion/checkpoint.json` | Where to save progress |
| `POLL_INTERVAL_SECONDS` | `60` | Polling frequency |
| `BATCH_SIZE` | `500` | Documents per query |
| `MAX_RETRIES` | `5` | Exponential backoff retries |

Example `.env`:
```bash
DATABASE_URL=postgresql://awh_user:secure_password@postgres.example.com:5432/awh_db
FIREBASE_CREDENTIALS_PATH=/opt/awh-ingestion/firebase-key.json
CHECKPOINT_PATH=/var/lib/awh-ingestion/checkpoint.json
POLL_INTERVAL_SECONDS=60
BATCH_SIZE=500
```

## Features

### Idempotency

Documents are inserted with:
```sql
ON CONFLICT (time, station_id) DO NOTHING
```

**Why this works:**
- Same timestamp + station = same measurement
- Reprocessing after crash automatically skips duplicates
- No application-level dedup needed

### Checkpointing

Checkpoint file (`checkpoint.json`) tracks:
- Last processed timestamp
- Total documents processed
- Last update time

**Behavior:**
- Loads on startup, resumes from that point
- Updates only AFTER successful batch insert
- Survives worker crashes

Example:
```json
{
  "last_processed_timestamp": "2025-10-02T13:15:53.937Z",
  "processed_count": 45730,
  "last_update": "2025-10-02T13:16:02.000Z"
}
```

### Station Management

Stations are auto-created from Firebase `station_name` field:

1. Check in-memory cache (fast)
2. Query database if not cached
3. Insert if missing (with `ON CONFLICT DO UPDATE`)
4. Cache the result

**Performance:** After first run, 100% of lookups are cache hits.

### Batch Processing

- Fetches up to `BATCH_SIZE` documents per cycle
- Inserts all in one transaction
- Atomically updates checkpoint only if insert succeeds
- Polling interval: 60 seconds (configurable)

**Throughput:**
- 500 docs/cycle × 60 cycles/hour = 30,000 docs/hour
- Scales to 100K+ with larger batch size

## Deployment

### Local Development (Single Process)

```bash
python ingestion_worker.py
```

### Production VM (Systemd)

```bash
# Copy DEPLOYMENT_GUIDE.md for full instructions
./install.sh  # Creates systemd unit
systemctl start awh-ingestion
```

### Production Cloud (Cloud Run)

```bash
gcloud run deploy awh-ingestion \
  --image gcr.io/PROJECT/awh-ingestion \
  --min-instances 1 --max-instances 1 \
  --memory 512Mi
```

See `DEPLOYMENT_GUIDE.md` for detailed steps.

## Monitoring

### Health Check

```bash
# Is worker running?
systemctl status awh-ingestion

# Are documents being processed?
psql -c "SELECT MAX(time) FROM measurements;"

# Check checkpoint age
stat /var/lib/awh-ingestion/checkpoint.json | grep Modify
```

### Key Metrics

```sql
-- Latest measurements (should be recent)
SELECT MAX(time) FROM measurements;

-- Documents per hour (last 24 hours)
SELECT DATE_TRUNC('hour', time), COUNT(*)
FROM measurements
WHERE time > NOW() - INTERVAL '24 hours'
GROUP BY 1 ORDER BY 1 DESC;

-- Stations and their counts
SELECT s.station_name, COUNT(m.*)
FROM measurements m
JOIN stations s ON m.station_id = s.station_id
WHERE m.time > NOW() - INTERVAL '7 days'
GROUP BY s.station_name
ORDER BY COUNT(*) DESC;
```

### Log Analysis

```bash
# Follow logs
tail -f /var/log/awh-ingestion.log

# Count errors
grep ERROR /var/log/awh-ingestion.log | wc -l

# Show retries
grep RETRY /var/log/awh-ingestion.log
```

## Error Handling

| Error | Cause | Recovery |
|-------|-------|----------|
| **Firebase Unavailable** | Network/API issue | Exponential backoff, retry from checkpoint |
| **PostgreSQL Unavailable** | DB down | Backoff, reconnect, retry same batch |
| **Malformed JSON** | Corrupt document | Log warning, skip document, continue |
| **Partial Batch Failure** | Some inserts fail | Rollback entire batch, retry |
| **Checkpoint Corruption** | File damage | Fallback to epoch, manual recovery |

All errors are:
- Logged with full context
- Non-fatal (worker continues)
- Retried with exponential backoff (1, 2, 4, 8, 16 seconds)

## Testing

Unit tests with pytest:

```bash
pip install pytest pytest-mock
pytest test_ingestion_worker.py -v
```

Tests cover:
- Checkpoint save/load/corruption
- Station cache and lookup
- Batch insertion and idempotency
- Error handling and retries
- Edge cases (missing fields, malformed data)

Integration test (requires real DB):
```bash
# Set up test database
createdb awh_test
psql awh_test < schema_timescaledb.sql

# Run integration test
pytest test_integration.py -v
```

## Troubleshooting

### No documents being ingested

```bash
# Check checkpoint
cat /var/lib/awh-ingestion/checkpoint.json

# Is Firebase collection correct?
# (Verify in Firebase Console)

# Are timestamps in Firebase newer than checkpoint?
```

### Duplicate rows appearing

```sql
-- Check for missing unique constraint
SELECT constraint_name FROM information_schema.table_constraints
WHERE table_name = 'measurements' AND constraint_type = 'UNIQUE';

-- If missing, create it manually:
ALTER TABLE measurements ADD CONSTRAINT measurements_time_station_key
UNIQUE (time, station_id);
```

### Worker crashes repeatedly

```bash
# Check logs for actual error
tail -50 /var/log/awh-ingestion.log

# Common causes:
# 1. DATABASE_URL invalid → psql connection test
# 2. Firebase key invalid → check file permissions and content
# 3. Checkpoint file corrupted → delete and restart
```

## Performance Tuning

### For 10K+ documents/hour

```bash
# Increase batch size
export BATCH_SIZE=5000

# Tune PostgreSQL (in postgresql.conf)
work_mem = 256MB
maintenance_work_mem = 1GB
shared_buffers = 4GB
```

### For 100K+ documents/hour

```bash
# Use COPY instead of INSERT (10x faster)
# Run multiple workers, each polling specific stations
# Consider connection pooling (PgBouncer)
# Add read replicas for analytics queries
```

## Future Evolution

### Redis Caching
Cache station lookups to reduce DB queries to near-zero:
```python
redis.set(f"station:{name}", station_id)
```

### ML Pipelines
Query raw data directly:
```sql
SELECT temperature, humidity, velocity
FROM measurements
WHERE station_id = 3 AND time > NOW() - INTERVAL '30 days'
```

### Incremental Aggregations
```sql
CREATE MATERIALIZED VIEW hourly_averages AS
SELECT time_bucket('1 hour', time), station_id, AVG(temperature)
FROM measurements
GROUP BY 1, 2;
```

## Files

| File | Purpose |
|------|---------|
| `ingestion_worker.py` | Main worker code |
| `requirements_ingestion.txt` | Python dependencies |
| `schema_timescaledb.sql` | Database schema setup |
| `DEPLOYMENT_GUIDE.md` | Production deployment instructions |
| `test_ingestion_worker.py` | Unit and integration tests |
| `README.md` | This file |

## License

Part of the AWH monitoring system. All rights reserved.

## Support

For issues, check:
1. Logs: `/var/log/awh-ingestion.log`
2. Checkpoint: `/var/lib/awh-ingestion/checkpoint.json`
3. Database: `SELECT * FROM stations; SELECT COUNT(*) FROM measurements;`
