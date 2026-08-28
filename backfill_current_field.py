"""
One-time backfill: populate the `current` column in Postgres `measurements`
for rows ingested before ingestion_worker.py started writing it.

The `current` (amperage) sensor field was never included in the worker's
INSERT statement, so every historical row has it NULL even though Firestore
has real values (confirmed: 100% of station_AquaPars@PowerPlant's last 500
readings have a non-null current). This walks each station's Firestore
history once and UPDATEs the matching Postgres row by (station_id, time).

Safe to re-run — UPDATEs are idempotent (re-writing the same value is a
no-op in effect), so an interrupted run can just be restarted.

Usage:
    python3 backfill_current_field.py
"""

import os
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import execute_values
import firebase_admin
from firebase_admin import credentials, firestore

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mounusha@localhost:5432/awh_db")
FIREBASE_CREDENTIALS_PATH = os.getenv(
    "FIREBASE_CREDENTIALS_PATH",
    os.path.join(os.path.dirname(__file__), "awh_az/backend/awh-project-460421-52cd6ebf2aa3.json"),
)
BATCH_SIZE = 5000


def backfill_station(db, pg_conn, station_id: int, station_name: str) -> int:
    readings_ref = (
        db.collection("stations")
        .document(station_name)
        .collection("readings")
        .select(["timestamp", "current"])
        .order_by("timestamp", direction=firestore.Query.ASCENDING)
    )

    total_updated = 0
    cursor_ts = None

    while True:
        query = readings_ref
        if cursor_ts is not None:
            query = query.start_after({"timestamp": cursor_ts})
        batch = list(query.limit(BATCH_SIZE).stream())
        if not batch:
            break

        rows = []
        for doc in batch:
            data = doc.to_dict()
            ts = data.get("timestamp")
            current = data.get("current")
            if ts is not None:
                cursor_ts = ts
            if current is not None and ts is not None:
                rows.append((ts.astimezone(timezone.utc), current))

        if rows:
            # station_id comes from our own `stations` table lookup, not
            # external input — safe to interpolate directly. execute_values
            # only supports one %s placeholder (the VALUES list) per call,
            # so station_id can't go through it as a separate bound param.
            with pg_conn.cursor() as cur:
                execute_values(
                    cur,
                    f"""
                    UPDATE measurements AS m SET current = v.current
                    FROM (VALUES %s) AS v(time, current)
                    WHERE m.station_id = {station_id} AND m.time = v.time
                    """,
                    rows,
                    template="(%s, %s)",
                )
            pg_conn.commit()
            total_updated += len(rows)

        if len(batch) < BATCH_SIZE:
            break

    return total_updated


def main() -> None:
    cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    pg_conn = psycopg2.connect(DATABASE_URL)
    try:
        with pg_conn.cursor() as cur:
            cur.execute("SELECT station_id, station_name FROM stations ORDER BY station_name")
            stations = cur.fetchall()

        for station_id, station_name in stations:
            n = backfill_station(db, pg_conn, station_id, station_name)
            print(f"{station_name}: {n} rows backfilled with a current value")
    finally:
        pg_conn.close()


if __name__ == "__main__":
    main()
