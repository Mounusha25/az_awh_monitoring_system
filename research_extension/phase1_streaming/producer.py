"""
AWH Kafka Producer — Phase 1: Streaming Foundation

Reads sensor measurements from PostgreSQL and publishes them to the
Kafka topic 'awh-sensor-readings' in chronological order, simulating
real-time data arrival for Spark Structured Streaming to consume.

Two modes:
  replay  — replay all historical data at a controlled rate (default)
  live    — tail new records from PostgreSQL as they arrive (production mode)

Usage:
  python producer.py               # replay mode, default speed
  python producer.py --mode live   # live tail mode
  python producer.py --speed 0     # replay as fast as possible (no delay)
  python producer.py --since "2025-11-01"  # replay from a specific date
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from kafka import KafkaProducer
from kafka.errors import NativeError
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL    = os.getenv("DATABASE_URL",  "postgresql://mounusha@localhost:5432/awh_db")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
TOPIC           = "awh-sensor-readings"

# 10 ML features — flow excluded (no sensor installed at active stations)
FEATURE_COLUMNS = [
    "temperature", "humidity", "velocity",
    "outtake_temperature", "outtake_humidity", "outtake_velocity",
    "weight", "voltage", "power", "energy",
]

REPLAY_SPEED_FACTOR = float(os.getenv("REPLAY_SPEED", "0.01"))  # 0.01 = 100× faster than real time
BATCH_FETCH_SIZE    = 500   # rows per DB fetch
LIVE_POLL_INTERVAL  = 5     # seconds between DB polls in live mode


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def row_to_message(row: dict) -> bytes:
    """Serialize a measurement row to JSON bytes for Kafka."""
    payload = {
        "station_id": row["station_id"],
        "time":       row["time"].isoformat() if hasattr(row["time"], "isoformat") else str(row["time"]),
    }
    for col in FEATURE_COLUMNS:
        val = row.get(col)
        payload[col] = float(val) if val is not None else None

    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _key(station_id: int) -> bytes:
    """Kafka message key — routes all readings from one station to the same partition."""
    return str(station_id).encode("utf-8")


# ---------------------------------------------------------------------------
# Replay Mode
# ---------------------------------------------------------------------------

def replay_historical(producer: KafkaProducer, conn, since: str, speed: float):
    """Replay all measurements since `since` in time order."""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute(
        "SELECT COUNT(*) AS n FROM measurements WHERE time >= %s", (since,)
    )
    total = cursor.fetchone()["n"]
    print(f"[Producer] Replaying {total:,} records from {since} at {1/speed:.0f}× real time")
    print(f"[Producer] Publishing to topic: {TOPIC}")

    cursor.execute(
        f"""
        SELECT m.time, m.station_id,
               {', '.join(f'm.{c}' for c in FEATURE_COLUMNS)}
        FROM measurements m
        WHERE m.time >= %s
        ORDER BY m.time ASC
        """,
        (since,)
    )

    prev_ts = None
    published = 0

    with tqdm(total=total, unit="msg", desc="Replaying") as bar:
        while True:
            rows = cursor.fetchmany(BATCH_FETCH_SIZE)
            if not rows:
                break

            for row in rows:
                # Simulate original timing at scaled speed
                if speed > 0 and prev_ts is not None:
                    delta = (row["time"] - prev_ts).total_seconds()
                    delay = delta * speed
                    if delay > 0:
                        time.sleep(min(delay, 1.0))  # cap individual sleep to 1s

                producer.send(
                    TOPIC,
                    key=_key(row["station_id"]),
                    value=row_to_message(row),
                )
                prev_ts = row["time"]
                published += 1
                bar.update(1)

    producer.flush()
    print(f"\n[Producer] Done — published {published:,} messages to {TOPIC}")


# ---------------------------------------------------------------------------
# Live Mode
# ---------------------------------------------------------------------------

def tail_live(producer: KafkaProducer, conn):
    """Continuously poll PostgreSQL for new records and publish them."""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Start from the latest record in the DB
    cursor.execute("SELECT MAX(time) FROM measurements")
    row = cursor.fetchone()
    checkpoint = row[0] if row[0] else datetime(2000, 1, 1, tzinfo=timezone.utc)

    print(f"[Producer] Live mode — tailing from {checkpoint}")
    print(f"[Producer] Publishing to topic: {TOPIC}")

    while True:
        cursor.execute(
            f"""
            SELECT m.time, m.station_id,
                   {', '.join(f'm.{c}' for c in FEATURE_COLUMNS)}
            FROM measurements m
            WHERE m.time > %s
            ORDER BY m.time ASC
            LIMIT %s
            """,
            (checkpoint, BATCH_FETCH_SIZE)
        )
        rows = cursor.fetchall()

        if rows:
            for row in rows:
                producer.send(
                    TOPIC,
                    key=_key(row["station_id"]),
                    value=row_to_message(row),
                )
                checkpoint = row["time"]

            producer.flush()
            print(f"[Producer] Published {len(rows)} new records (latest: {checkpoint})")
        else:
            print(f"[Producer] No new records — polling again in {LIVE_POLL_INTERVAL}s")
            time.sleep(LIVE_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AWH Kafka Producer")
    parser.add_argument(
        "--mode",
        choices=["replay", "live"],
        default="replay",
        help="replay: replay historical data  |  live: tail new records",
    )
    parser.add_argument(
        "--since",
        default="1970-01-01",
        help="Replay start date (ISO format, e.g. 2025-11-01). Ignored in live mode.",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=REPLAY_SPEED_FACTOR,
        help="Time-scaling factor for replay (0 = no delay, 0.01 = 100× faster than real time)",
    )
    args = parser.parse_args()

    # Connect to Kafka
    print(f"[Producer] Connecting to Kafka at {KAFKA_BOOTSTRAP}...")
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        acks="all",
        retries=5,
        max_in_flight_requests_per_connection=1,  # preserve ordering
        value_serializer=None,  # we pre-serialize to bytes
        key_serializer=None,
    )
    print("[Producer] Kafka connected")

    # Connect to PostgreSQL
    print(f"[Producer] Connecting to PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL)
    print("[Producer] PostgreSQL connected")

    try:
        if args.mode == "replay":
            replay_historical(producer, conn, args.since, args.speed)
        else:
            tail_live(producer, conn)
    except KeyboardInterrupt:
        print("\n[Producer] Interrupted")
    finally:
        producer.close()
        conn.close()


if __name__ == "__main__":
    main()
