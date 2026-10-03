"""Durable, non-blocking cloud uploader for an AWH station.

Replaces the old inline `send_to_cloud()`, which made one blocking POST inside the
1-second save loop and silently dropped the reading on any failure.

How it works
  * submit() writes the reading to a small on-disk SQLite queue and returns at once,
    so a network outage can never stall CSV logging or the UI.
  * A background thread sends the oldest queued reading first, one at a time. Success
    deletes it; a network error or HTTP 5xx keeps it and retries with backoff
    (5 s doubling to 1 min); a permanent HTTP 4xx drops it (it would never succeed).
  * Every reading gets a stable reading_id, so a retry after an ambiguous timeout
    cannot create a duplicate (the Cloud Function uses it as the document id).
  * The queue is on disk, so readings survive a reboot or power loss.

Timestamps (see cloud_functions/receive_data/main.py)
  * A reading sent within LIVE_MAX_AGE_SEC of capture is "live": no timestamp is sent
    and the server stamps it, exactly as before.
  * An older reading is a replay. It is sent with its capture time ONLY if the Pi's
    clock was NTP-synced when captured (a Pi has no real-time clock, so an unsynced
    clock can be badly wrong). Otherwise it is dropped here rather than stored with a
    wrong time; it is still in the local CSV.

Authentication: if a station key is configured (env AWH_STATION_KEY, or the first line of
station_state/station_key next to the queue) it is sent in the X-Station-Key header.
HTTP 401/403 is treated as RETRYABLE, not permanent: a wrong or rotated key must keep
readings queued until someone fixes it, never silently discard them. The key is never logged.

Requires cloud_functions/receive_data to be deployed with reading_id/replayed
support first. Against the old function it still works but a replay would be
stamped with the upload time, so deploy the function before using this.
"""
import json
import logging
import logging.handlers
import os
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone

import requests

LIVE_MAX_AGE_SEC = 120
BACKOFF_START_SEC = 5
BACKOFF_MAX_SEC = 60   # one retry a minute is no heavier than normal uploads; keeps post-outage recovery under ~1-2 min
REQUEST_TIMEOUT_SEC = 10
MAX_QUEUE_ROWS = 100_000          # ~69 days at one reading a minute
RETRYABLE_4XX = {401, 403, 408, 429}   # 401/403: bad/rotated station key, fixable by an operator
KEY_HEADER = "X-Station-Key"
CLOCK_CACHE_SEC = 60

log = logging.getLogger("awh.uploader")


# ---------------------------------------------------------------------------
# Logging to a rotating file (the station scripts only print() otherwise)
# ---------------------------------------------------------------------------
def setup_file_logging(log_dir, filename="station.log"):
    """Log awh.* messages to a rotating file (1 MB x 5) and the console. Idempotent."""
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("awh")
    logger.setLevel(logging.INFO)
    if any(getattr(h, "_awh_station", False) for h in logger.handlers):
        return logger
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(os.path.join(log_dir, filename), maxBytes=1_000_000, backupCount=5)
    sh = logging.StreamHandler()
    for h in (fh, sh):
        h.setFormatter(fmt)
        h._awh_station = True
        logger.addHandler(h)
    return logger


# ---------------------------------------------------------------------------
# Clock sync check
# ---------------------------------------------------------------------------
_clock_cache = {"at": 0.0, "value": False}


def is_clock_synced(run=subprocess.run, now=time.time):
    """True if the OS reports NTP-synchronised time. Conservative: False on any doubt."""
    if now() - _clock_cache["at"] < CLOCK_CACHE_SEC:
        return _clock_cache["value"]
    try:
        out = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                  capture_output=True, text=True, timeout=5).stdout.strip()
        value = out == "yes"
    except Exception:
        value = False
    _clock_cache.update(at=now(), value=value)
    return value


# ---------------------------------------------------------------------------
# Station key
# ---------------------------------------------------------------------------
def load_station_key(key_path, env=os.environ):
    """AWH_STATION_KEY env var wins; else the first line of key_path; else None."""
    key = (env.get("AWH_STATION_KEY") or "").strip()
    if key:
        return key
    try:
        with open(key_path, "r", encoding="utf-8") as f:
            return f.readline().strip() or None
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Durable queue
# ---------------------------------------------------------------------------
class ReadingQueue:
    def __init__(self, path, max_rows=MAX_QUEUE_ROWS):
        self.path, self.max_rows = path, max_rows
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        try:
            self._open()
        except sqlite3.DatabaseError:
            # SD cards and power cuts happen. Keep the bad file for inspection, start fresh.
            bad = f"{path}.corrupt-{int(time.time())}"
            log.error("upload queue %s is corrupt; moving it to %s and starting empty", path, bad)
            os.replace(path, bad)
            self._open()

    def _open(self):
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS readings ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, reading_id TEXT UNIQUE NOT NULL,"
            " station TEXT NOT NULL, payload TEXT NOT NULL, captured_at REAL NOT NULL,"
            " clock_synced INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)")
        self._db.commit()
        self._db.execute("SELECT COUNT(*) FROM readings").fetchone()  # raises on a corrupt file

    def add(self, station, payload, captured_at, clock_synced):
        reading_id = uuid.uuid4().hex
        with self._lock:
            self._db.execute(
                "INSERT INTO readings (reading_id, station, payload, captured_at, clock_synced) VALUES (?,?,?,?,?)",
                (reading_id, station, json.dumps(payload), captured_at, int(bool(clock_synced))))
            over = self._db.execute("SELECT COUNT(*) FROM readings").fetchone()[0] - self.max_rows
            if over > 0:
                self._db.execute("DELETE FROM readings WHERE id IN (SELECT id FROM readings ORDER BY id LIMIT ?)", (over,))
                log.warning("upload queue full (%d); dropped %d oldest reading(s)", self.max_rows, over)
            self._db.commit()
        return reading_id

    def oldest(self):
        with self._lock:
            row = self._db.execute(
                "SELECT id, reading_id, station, payload, captured_at, clock_synced, attempts "
                "FROM readings ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        keys = ("id", "reading_id", "station", "payload", "captured_at", "clock_synced", "attempts")
        return dict(zip(keys, row))

    def delete(self, row_id):
        with self._lock:
            self._db.execute("DELETE FROM readings WHERE id=?", (row_id,))
            self._db.commit()

    def bump_attempts(self, row_id):
        with self._lock:
            self._db.execute("UPDATE readings SET attempts=attempts+1 WHERE id=?", (row_id,))
            self._db.commit()

    def __len__(self):
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM readings").fetchone()[0]

    def close(self):
        with self._lock:
            self._db.close()


# ---------------------------------------------------------------------------
# Uploader
# ---------------------------------------------------------------------------
class CloudUploader:
    def __init__(self, url, queue_path, post=requests.post, clock_synced=is_clock_synced,
                 time_fn=time.time, max_queue_rows=MAX_QUEUE_ROWS, station_key=None):
        self.url = url
        key = station_key or load_station_key(os.path.join(os.path.dirname(os.path.abspath(queue_path)), "station_key"))
        self._headers = {KEY_HEADER: key} if key else {}
        if not key:
            log.warning("no station key configured (AWH_STATION_KEY or station_state/station_key); "
                        "uploads are unauthenticated and will be rejected once the cloud enforces keys")
        self.queue = ReadingQueue(queue_path, max_queue_rows)
        self._post = post
        self._clock_synced = clock_synced
        self._time = time_fn
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._failing = False
        self.last_success_ts = None
        self.last_error = None

    # ----- producer side (called from the 1-second save loop; must never block or raise)
    def submit(self, station_name, payload):
        try:
            self.queue.add(station_name, payload, self._time(), self._clock_synced())
            self._wake.set()
            return True
        except Exception:
            log.exception("could not queue reading for upload")
            return False

    def stats(self):
        return {"queued": len(self.queue), "last_success_ts": self.last_success_ts,
                "last_error": self.last_error, "failing": self._failing}

    # ----- consumer side
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cloud-uploader", daemon=True)
        self._thread.start()

    def stop(self, timeout=5):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def _build_body(self, row):
        """Return the POST body, or None to drop a late reading whose time can't be trusted."""
        body = json.loads(row["payload"])
        body["station_name"] = row["station"]
        body["reading_id"] = row["reading_id"]
        age = self._time() - row["captured_at"]
        if age > LIVE_MAX_AGE_SEC:
            if not row["clock_synced"]:
                return None
            body["replayed"] = True
            body["client_timestamp"] = datetime.fromtimestamp(row["captured_at"], tz=timezone.utc).isoformat()
        return body

    def attempt_next(self):
        """Try to send the oldest reading. Returns 'empty' | 'sent' | 'dropped' | 'retry'."""
        row = self.queue.oldest()
        if row is None:
            return "empty"

        body = self._build_body(row)
        if body is None:
            log.warning("dropping reading captured %.0f s ago with an unsynced clock "
                        "(time untrusted; still in the local CSV)", self._time() - row["captured_at"])
            self.queue.delete(row["id"])
            return "dropped"

        try:
            resp = self._post(self.url, json=body, headers=self._headers, timeout=REQUEST_TIMEOUT_SEC)
            status = resp.status_code
        except Exception as e:
            return self._retry(row, f"{type(e).__name__}: {e}")

        if 200 <= status < 300:
            if self._failing:
                log.info("upload recovered; %d reading(s) still queued", max(len(self.queue) - 1, 0))
            self._failing = False
            self.last_success_ts = self._time()
            self.last_error = None
            self.queue.delete(row["id"])
            # INFO (not DEBUG): this is the operator's "it's working" signal, replacing the old
            # "[Cloud Upload] 200" console line. ~1 line/minute; the rotating log keeps weeks of it.
            log.info("[Cloud Upload] %s OK - reading sent (%d still queued)", status, max(len(self.queue), 0))
            return "sent"
        if 400 <= status < 500 and status not in RETRYABLE_4XX:
            log.error("cloud rejected a reading permanently (HTTP %s); dropping it: %s",
                      status, getattr(resp, "text", "")[:200])
            self.queue.delete(row["id"])
            return "dropped"
        if status in (401, 403):
            return self._retry(row, f"HTTP {status}: station key rejected - check station_state/station_key")
        return self._retry(row, f"HTTP {status}")

    def _retry(self, row, why):
        self.queue.bump_attempts(row["id"])
        self.last_error = why
        if not self._failing:
            log.warning("upload failing (%s); readings are being queued and will be retried", why)
        self._failing = True
        return "retry"

    def _run(self):
        backoff = BACKOFF_START_SEC
        while not self._stop.is_set():
            self._wake.clear()
            try:
                result = self.attempt_next()
            except Exception:
                log.exception("uploader error")
                result = "retry"
            if result == "empty":
                self._wake.wait()
            elif result == "retry":
                self._stop.wait(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SEC)
            else:
                backoff = BACKOFF_START_SEC  # sent/dropped: go straight on to the next one
