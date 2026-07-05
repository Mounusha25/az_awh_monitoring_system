"""
Firebase → PostgreSQL/TimescaleDB Ingestion Worker

Moves measurement data from Firebase (Firestore) to PostgreSQL/TimescaleDB
with idempotency, checkpointing, and error recovery.

Production-grade design for AWH monitoring system.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values
import firebase_admin
from firebase_admin import credentials, firestore


# ============================================
# Configuration
# ============================================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mounusha@localhost:5432/awh_db"
)

FIREBASE_CREDENTIALS_PATH = os.getenv(
    "FIREBASE_CREDENTIALS_PATH",
    os.path.join(os.path.dirname(__file__), "awh_az/backend/awh-project-460421-52cd6ebf2aa3.json")
)

CHECKPOINT_PATH = os.getenv(
    "CHECKPOINT_PATH",
    os.path.expanduser("~/.awh-ingestion/checkpoint.json")
)

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "4000"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))

# ============================================
# Logging Setup
# ============================================

# Determine log file location (prefer /var/log, fallback to home dir)
log_file = os.getenv(
    "LOG_FILE",
    "/var/log/awh-ingestion.log" if os.access("/var/log", os.W_OK) 
    else os.path.expanduser("~/.awh-ingestion.log")
)

handlers = [logging.StreamHandler()]
try:
    handlers.append(logging.FileHandler(log_file))
except (PermissionError, OSError):
    logger_setup = logging.getLogger(__name__)
    logger_setup.warning(f"Could not write to {log_file}, using stdout only")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=handlers
)
logger = logging.getLogger(__name__)


# ============================================
# Checkpoint Management
# ============================================

class CheckpointManager:
    """Manage ingestion checkpoint state."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self):
        """Load checkpoint or return epoch zero."""
        try:
            with open(self.path) as f:
                data = json.load(f)
                logger.info(
                    f"Loaded checkpoint: {data['last_processed_timestamp']} "
                    f"({data['processed_count']} docs)"
                )
                return data['last_processed_timestamp']
        except FileNotFoundError:
            logger.info("No checkpoint found, starting from epoch zero")
            return "1970-01-01T00:00:00.000Z"
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Checkpoint corrupted: {e}, starting from epoch")
            return "1970-01-01T00:00:00.000Z"

    def save(self, timestamp, count):
        """Atomically save checkpoint."""
        temp_path = self.path.with_suffix('.tmp')

        data = {
            'last_processed_timestamp': timestamp,
            'processed_count': count,
            'last_update': datetime.now(timezone.utc).isoformat()
        }

        try:
            with open(temp_path, 'w') as f:
                json.dump(data, f)
            temp_path.replace(self.path)  # Atomic rename on POSIX
            logger.debug(f"Checkpoint saved: {timestamp}")
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
            raise


# ============================================
# Firebase Client
# ============================================

class FirebaseClient:
    """Firebase Firestore document fetcher."""

    def __init__(self, credentials_path):
        self.credentials_path = credentials_path
        self._initialize()

    def _initialize(self):
        """Initialize Firebase app and get Firestore client."""
        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(self.credentials_path)
                firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            logger.info("Firebase initialized")
        except Exception as e:
            logger.error(f"Firebase initialization failed: {e}")
            raise

    def fetch_new_documents(self, since_timestamp, limit=BATCH_SIZE):
        """
        Query Firestore for documents newer than checkpoint.

        Firestore structure: stations/{station_name}/readings/{doc_id}

        Queries each station's 'readings' subcollection individually, then
        merges and sorts by timestamp. This avoids requiring a Firestore
        collection-group index on 'timestamp'.

        To switch to a single collection_group query (faster for large backlogs),
        create the index at:
        https://console.firebase.google.com/project/awh-project-460421/firestore/indexes
        — add a Collection Group index on 'readings' with field 'timestamp' ASC —
        then replace this method body with:
            return list(
                self.db.collection_group('readings')
                .where('timestamp', '>', since_timestamp)
                .order_by('timestamp')
                .limit(limit)
                .stream()
            )

        Args:
            since_timestamp: ISO 8601 string (e.g., "2025-10-02T13:15:53.937Z")
            limit: Max documents to fetch per query

        Returns:
            List of document dicts or empty list on error
        """
        try:
            stations = list(self.db.collection('stations').list_documents())
            if not stations:
                logger.warning("No stations found in Firestore")
                return []

            # Firestore stores 'timestamp' as a Timestamp object, not a string.
            # Convert the ISO checkpoint string to a timezone-aware datetime.
            if isinstance(since_timestamp, str):
                since_dt = datetime.fromisoformat(
                    since_timestamp.replace('Z', '+00:00')
                )
            else:
                since_dt = since_timestamp

            per_station = max(limit // len(stations), 50)
            all_docs = []

            for sdoc in stations:
                docs = list(
                    sdoc.collection('readings')
                    .where('timestamp', '>', since_dt)
                    .order_by('timestamp')
                    .limit(per_station)
                    .stream()
                )
                all_docs.extend([d.to_dict() for d in docs])

            # Sort globally by timestamp, honour the overall batch limit
            all_docs.sort(key=lambda x: x.get('timestamp', ''))
            result = all_docs[:limit]

            logger.info(
                f"Fetched {len(result)} documents from Firestore "
                f"({len(stations)} stations, {per_station} per station)"
            )
            return result

        except Exception as e:
            logger.error(f"Firestore query failed: {e}")
            return []


# ============================================
# PostgreSQL Station Management
# ============================================

class StationManager:
    """Manage station lookups and creation with in-memory cache."""

    def __init__(self, conn):
        self.conn = conn
        self.cache = {}  # {station_name: station_id}
        self._load_cache()

    def _load_cache(self):
        """Load all stations into cache on startup."""
        try:
            with self.conn.cursor() as cursor:
                cursor.execute("SELECT station_name, station_id FROM stations")
                for name, sid in cursor.fetchall():
                    self.cache[name] = sid
                logger.info(f"Loaded {len(self.cache)} stations into cache")
        except Exception as e:
            logger.error(f"Failed to load station cache: {e}")

    def get_or_create(self, station_name, cursor):
        """
        Get station_id, insert if new.

        Args:
            station_name: Human-readable station identifier
            cursor: psycopg2 cursor in active transaction

        Returns:
            station_id (int)
        """
        # Check cache first
        if station_name in self.cache:
            return self.cache[station_name]

        # Check database
        cursor.execute(
            "SELECT station_id FROM stations WHERE station_name = %s",
            (station_name,)
        )
        row = cursor.fetchone()

        if row:
            station_id = row[0]
            logger.debug(f"Found existing station: {station_name} (ID: {station_id})")
        else:
            # Insert new station
            location = self._extract_location(station_name)
            cursor.execute(
                """
                INSERT INTO stations (station_name, location, created_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (station_name) DO NOTHING
                RETURNING station_id
                """,
                (station_name, location)
            )
            station_id = cursor.fetchone()[0]
            logger.info(f"Created new station: {station_name} (ID: {station_id})")

        # Cache it
        self.cache[station_name] = station_id
        return station_id

    @staticmethod
    def _extract_location(station_name):
        """Extract location from station name (heuristic)."""
        # Example: "station_AquaPars #2 @Power Station, Tempe" -> "Power Station, Tempe"
        if '@' in station_name:
            return station_name.split('@', 1)[1].strip()
        return station_name


# ============================================
# PostgreSQL Measurement Insert
# ============================================

class MeasurementInserter:
    """Bulk insert measurements with idempotency."""

    def __init__(self, conn, station_manager):
        self.conn = conn
        self.station_manager = station_manager
        self.total_inserted = 0

    def insert_batch(self, documents):
        """
        Insert batch of Firebase documents into PostgreSQL.

        Idempotent: ON CONFLICT (time, station_id) DO NOTHING

        Args:
            documents: List of Firebase document dicts

        Returns:
            Number of documents processed (may be less than inserted due to conflicts)
        """
        if not documents:
            return 0

        try:
            with self.conn.cursor() as cursor:
                rows = []

                for doc in documents:
                    try:
                        # Get or create station
                        if 'station_name' not in doc:
                            logger.warning(f"Document missing station_name: {doc}")
                            continue

                        station_id = self.station_manager.get_or_create(
                            doc['station_name'],
                            cursor
                        )

                        # Build row tuple
                        row = (
                            doc['timestamp'],
                            station_id,
                            doc.get('temperature'),
                            doc.get('humidity'),
                            doc.get('velocity'),
                            doc.get('unit'),
                            doc.get('outtake_temperature'),
                            doc.get('outtake_humidity'),
                            doc.get('outtake_velocity'),
                            doc.get('outtake_unit'),
                            doc.get('weight'),
                            doc.get('pump_status'),
                            doc.get('voltage'),
                            doc.get('power'),
                            doc.get('energy'),
                            doc.get('flow_lmin'),
                            doc.get('flow_hz'),
                            doc.get('flow_total')
                        )
                        rows.append(row)

                    except KeyError as e:
                        logger.warning(f"Malformed document, skipping: {e}")
                        continue

                # Bulk insert with idempotency
                if rows:
                    execute_values(
                        cursor,
                        """
                        INSERT INTO measurements (
                            time, station_id,
                            temperature, humidity, velocity, unit,
                            outtake_temperature, outtake_humidity, outtake_velocity, outtake_unit,
                            weight, pump_status,
                            voltage, power, energy,
                            flow_lmin, flow_hz, flow_total
                        ) VALUES %s
                        ON CONFLICT (time, station_id) DO NOTHING
                        """,
                        rows
                    )

                    self.conn.commit()
                    self.total_inserted += len(rows)
                    logger.info(f"Inserted {len(rows)} measurements (total: {self.total_inserted})")
                    return len(rows)

                return 0

        except Exception as e:
            self.conn.rollback()
            logger.error(f"Batch insert failed: {e}")
            raise


# ============================================
# Retry Logic
# ============================================

def retry_with_backoff(func, max_retries=MAX_RETRIES):
    """
    Execute function with exponential backoff retry.

    Args:
        func: Callable to execute
        max_retries: Number of retries (total attempts = max_retries + 1)

    Returns:
        Result of func() or None if all retries exhausted
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            if attempt == max_retries:
                logger.error(f"Failed after {max_retries} retries: {e}")
                return None

            wait = 2 ** attempt  # 1, 2, 4, 8, 16 seconds
            logger.warning(
                f"Retry {attempt + 1}/{max_retries + 1} after {wait}s: {e}"
            )
            time.sleep(wait)


# ============================================
# Main Ingestion Loop
# ============================================

class IngestionWorker:
    """Main ingestion worker orchestrator."""

    def __init__(self):
        self.checkpoint = CheckpointManager(CHECKPOINT_PATH)
        self.firebase = FirebaseClient(FIREBASE_CREDENTIALS_PATH)
        self.conn = None
        self.station_manager = None
        self.inserter = None

    def _connect_db(self):
        """Establish PostgreSQL connection."""
        try:
            self.conn = psycopg2.connect(DATABASE_URL)
            logger.info("Connected to PostgreSQL")
            return True
        except Exception as e:
            logger.error(f"PostgreSQL connection failed: {e}")
            return False

    def initialize(self):
        """Initialize worker: connect to DB, load cache."""
        if not self._connect_db():
            raise RuntimeError("Failed to connect to PostgreSQL")

        self.station_manager = StationManager(self.conn)
        self.inserter = MeasurementInserter(self.conn, self.station_manager)
        logger.info("Worker initialized")

    def run_once(self, checkpoint_ts):
        """
        Execute one polling cycle.

        Args:
            checkpoint_ts: Last processed timestamp

        Returns:
            New checkpoint timestamp or original if no documents processed
        """
        # Fetch new documents
        batch = self.firebase.fetch_new_documents(
            checkpoint_ts,
            limit=BATCH_SIZE
        )

        if not batch:
            logger.debug("No new documents in this cycle")
            return checkpoint_ts

        # Insert to PostgreSQL
        self.inserter.insert_batch(batch)

        # Update checkpoint to max timestamp in batch.
        # Firestore returns DatetimeWithNanoseconds; convert to ISO string for JSON storage.
        max_ts = max(doc['timestamp'] for doc in batch)
        if hasattr(max_ts, 'isoformat'):
            new_checkpoint = max_ts.isoformat()
        else:
            new_checkpoint = str(max_ts)
        return new_checkpoint

    def main_loop(self):
        """Main ingestion loop."""
        current_checkpoint = self.checkpoint.load()

        while True:
            try:
                logger.debug(f"Polling from: {current_checkpoint}")
                new_checkpoint = self.run_once(current_checkpoint)

                if new_checkpoint != current_checkpoint:
                    self.checkpoint.save(new_checkpoint, self.inserter.total_inserted)
                    current_checkpoint = new_checkpoint

                logger.debug(f"Sleeping for {POLL_INTERVAL_SECONDS}s")
                time.sleep(POLL_INTERVAL_SECONDS)

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
                break

            except Exception as e:
                logger.error(f"Ingestion cycle failed: {e}", exc_info=True)
                time.sleep(10)  # Backoff on error

        self._cleanup()

    def _cleanup(self):
        """Clean up resources."""
        if self.conn:
            self.conn.close()
            logger.info("PostgreSQL connection closed")


# ============================================
# Entry Point
# ============================================

def main():
    """Main entry point."""
    worker = IngestionWorker()

    try:
        worker.initialize()
        worker.main_loop()
    except Exception as e:
        logger.critical(f"Worker failed to start: {e}", exc_info=True)
        exit(1)


if __name__ == "__main__":
    main()
