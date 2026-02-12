# Ingestion Worker Deployment Guide

## Quick Start

### 1. Database Setup

```bash
# Connect to PostgreSQL
psql -h localhost -U postgres -d awh_db

# Run schema setup
\i schema_timescaledb.sql

# Verify
SELECT * FROM stations;
SELECT COUNT(*) FROM measurements;
```

### 2. Firebase Credentials

```bash
# Download your Firebase service account key from:
# https://console.firebase.google.com → Project Settings → Service Accounts

cp /path/to/firebase-key.json /opt/awh-ingestion/

# Restrict permissions
chmod 600 /opt/awh-ingestion/firebase-key.json
```

### 3. Install Dependencies

```bash
pip install -r requirements_ingestion.txt
```

### 4. Environment Variables

Create `.env` file:

```bash
cat > /opt/awh-ingestion/.env << EOF
DATABASE_URL=postgresql://awh_user:secure_password@postgres.example.com:5432/awh_db
FIREBASE_CREDENTIALS_PATH=/opt/awh-ingestion/firebase-key.json
CHECKPOINT_PATH=/var/lib/awh-ingestion/checkpoint.json
POLL_INTERVAL_SECONDS=60
BATCH_SIZE=500
MAX_RETRIES=5
EOF

chmod 600 /opt/awh-ingestion/.env
```

### 5. Test Locally

```bash
# Source environment
export $(cat /opt/awh-ingestion/.env | xargs)

# Run worker
python ingestion_worker.py
```

Watch logs:
```
2025-10-02 13:16:02 [INFO] Firebase initialized
2025-10-02 13:16:03 [INFO] Connected to PostgreSQL
2025-10-02 13:16:03 [INFO] Loaded 2 stations into cache
2025-10-02 13:16:03 [INFO] Worker initialized
2025-10-02 13:16:03 [INFO] Loaded checkpoint: 2025-10-02T12:00:00.000Z (45627 docs)
2025-10-02 13:16:04 [INFO] Fetched 103 documents from Firebase
2025-10-02 13:16:04 [INFO] Inserted 103 measurements (total: 45730)
```

---

## Production Deployment

### Option A: Systemd Service (VM)

**1. Create service user:**
```bash
sudo useradd -r -s /bin/bash -d /opt/awh-ingestion awh-ingestion
sudo mkdir -p /opt/awh-ingestion /var/lib/awh-ingestion /var/log
sudo chown awh-ingestion:awh-ingestion /opt/awh-ingestion /var/lib/awh-ingestion /var/log/awh-ingestion.log
```

**2. Copy code:**
```bash
sudo cp ingestion_worker.py /opt/awh-ingestion/
sudo cp requirements_ingestion.txt /opt/awh-ingestion/
sudo cp .env /opt/awh-ingestion/
sudo chown awh-ingestion:awh-ingestion /opt/awh-ingestion/*
sudo chmod 600 /opt/awh-ingestion/.env
```

**3. Create systemd unit:**
```bash
sudo tee /etc/systemd/system/awh-ingestion.service > /dev/null << EOF
[Unit]
Description=AWH Firebase→PostgreSQL Ingestion Worker
After=network.target postgresql.service

[Service]
Type=simple
User=awh-ingestion
WorkingDirectory=/opt/awh-ingestion
EnvironmentFile=/opt/awh-ingestion/.env

ExecStart=/usr/bin/python3 /opt/awh-ingestion/ingestion_worker.py

Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Security
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes

[Install]
WantedBy=multi-user.target
EOF
```

**4. Enable and start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable awh-ingestion
sudo systemctl start awh-ingestion

# View logs
sudo journalctl -u awh-ingestion -f
```

---

### Option B: Cloud Run (Google Cloud)

**1. Create Dockerfile:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements_ingestion.txt .
RUN pip install --no-cache-dir -r requirements_ingestion.txt

# Copy code
COPY ingestion_worker.py .

# Run worker
CMD ["python", "ingestion_worker.py"]
```

**2. Build and push:**
```bash
gcloud builds submit --tag gcr.io/PROJECT_ID/awh-ingestion

gcloud run deploy awh-ingestion \
  --image gcr.io/PROJECT_ID/awh-ingestion \
  --min-instances 1 \
  --max-instances 1 \
  --memory 512Mi \
  --timeout 3600 \
  --set-env-vars DATABASE_URL=postgresql://...,FIREBASE_CREDENTIALS_PATH=/path/to/key.json
```

**3. Checkpoint storage (Cloud Storage):**
```python
# Modify CheckpointManager to use Cloud Storage
from google.cloud import storage

class CheckpointManager:
    def __init__(self, bucket_name, blob_name):
        self.bucket = storage.Client().bucket(bucket_name)
        self.blob = self.bucket.blob(blob_name)

    def load(self):
        try:
            return json.loads(self.blob.download_as_string())
        except:
            return {}

    def save(self, timestamp, count):
        self.blob.upload_from_string(
            json.dumps({'timestamp': timestamp, 'count': count})
        )
```

---

## Monitoring

### Logs
```bash
# VM (systemd)
sudo journalctl -u awh-ingestion -f

# Cloud Run
gcloud run logs read awh-ingestion --limit 50 --follow
```

### Metrics to Alert On

```sql
-- Ingestion lag (documents older than this are not yet in DB)
SELECT MAX(time) FROM measurements;

-- Verify against Firebase timestamp

-- Documents per hour
SELECT
  DATE_TRUNC('hour', time) AS hour,
  COUNT(*) AS count
FROM measurements
GROUP BY hour
ORDER BY hour DESC
LIMIT 24;

-- Stations by document count
SELECT
  s.station_name,
  COUNT(m.time) AS measurements
FROM measurements m
JOIN stations s ON m.station_id = s.station_id
WHERE m.time > NOW() - INTERVAL '7 days'
GROUP BY s.station_name
ORDER BY measurements DESC;
```

### Health Check

```bash
# Is worker running?
systemctl status awh-ingestion

# Are documents being processed?
psql -c "SELECT MAX(time) FROM measurements;"

# Check checkpoint age
stat /var/lib/awh-ingestion/checkpoint.json | grep Modify
```

---

## Troubleshooting

### Checkpoint File Corrupted
```bash
# Reset to epoch zero (will reprocess all)
sudo rm /var/lib/awh-ingestion/checkpoint.json
sudo systemctl restart awh-ingestion
```

### Firebase Connection Fails
```
Check:
- Service account key is valid
- Firebase project has "measurements" collection
- Network connectivity to Firebase
```

### PostgreSQL Connection Fails
```
Check:
- DATABASE_URL is correct
- Database exists: psql -l
- Table exists: psql -c "\dt measurements"
- User permissions: psql -c "\du"
```

### Duplicate Documents Appearing
```
This should not happen (ON CONFLICT DO NOTHING prevents it).
If it does: Check if unique constraint is active:

psql -c "
SELECT constraint_name, constraint_type
FROM information_schema.table_constraints
WHERE table_name = 'measurements' AND constraint_type = 'UNIQUE';
"
```

---

## Performance Tuning

### High Volume (10K+ docs/hour)

**1. Increase batch size:**
```bash
export BATCH_SIZE=5000
```

**2. Use COPY instead of INSERT:**
```python
# Modify inserter to use COPY FROM STDIN
# (10x faster than INSERT VALUES)
```

**3. Tune PostgreSQL:**
```sql
ALTER DATABASE awh_db SET
  work_mem = '256MB',
  maintenance_work_mem = '1GB',
  shared_buffers = '4GB';
```

**4. Shard by station:**
```python
# Run multiple workers, each polling specific stations
# Example: worker1 polls station_id IN (1, 3, 5)
#          worker2 polls station_id IN (2, 4, 6)
```

---

## Rollback / Recovery

**If data is corrupted:**

```bash
# Restore from backup (assuming you have one)
psql awh_db < backup.sql

# Restart worker from manual checkpoint
echo '{"last_processed_timestamp": "2025-10-02T12:00:00.000Z"}' \
  > /var/lib/awh-ingestion/checkpoint.json
```

---

## Next Steps

1. **Test locally** with real Firebase data
2. **Deploy to VM** or Cloud Run
3. **Monitor logs** for 24 hours
4. **Set up alerts** for ingestion lag
5. **Schedule backups** of PostgreSQL
