"""
Sanity-check Postgres against live Firestore counts, per station.

The ingestion worker's checkpoint.json is not proof of completeness — it only
tracks the last timestamp *seen*, independent of whether every document up to
that point was actually inserted (see the 2026-08-17 desync where checkpoints
sat at the true Firestore max while ~960K rows were silently missing). This
compares real counts on both sides instead of trusting the checkpoint file.

Meant to run both interactively (full table, for manual audits) and on a
schedule (see edu.asu.awh-sync-check.plist alongside it) — a nonzero gap up
to MISSING_ROW_THRESHOLD is normal poll lag (Postgres is always a few rows
behind Firestore between ingestion cycles, observed ~4 rows on a live check);
only a gap above that threshold prints an [ALERT] line, so a scheduled daily
run doesn't cry wolf on ordinary lag while still catching a real desync like
the 2026-08-17 incident (~960K rows) automatically instead of by manual audit.

Usage:
    python3 check_ingestion_sync.py

Env vars:
    DATABASE_URL, FIREBASE_CREDENTIALS_PATH  same as ingestion_worker.py
    MISSING_ROW_THRESHOLD                    rows of gap before alerting (default 200)
"""

import os

import psycopg2
import firebase_admin
from firebase_admin import credentials, firestore

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mounusha@localhost:5432/awh_db"
)
FIREBASE_CREDENTIALS_PATH = os.getenv(
    "FIREBASE_CREDENTIALS_PATH",
    os.path.join(os.path.dirname(__file__), "awh_az/backend/awh-project-460421-52cd6ebf2aa3.json")
)
MISSING_ROW_THRESHOLD = int(os.getenv("MISSING_ROW_THRESHOLD", "200"))


def main():
    cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    firestore_counts = {}
    for sdoc in db.collection("stations").list_documents():
        agg = sdoc.collection("readings").count().get()
        firestore_counts[sdoc.id] = int(agg[0][0].value)

    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.station_name, count(*)
            FROM measurements m JOIN stations s ON s.station_id = m.station_id
            GROUP BY s.station_name
        """)
        postgres_counts = dict(cur.fetchall())
    conn.close()

    all_stations = sorted(set(firestore_counts) | set(postgres_counts))

    print(f"{'station':<45} {'firestore':>10} {'postgres':>10} {'missing':>10}")
    print("-" * 78)
    total_missing = 0
    for name in all_stations:
        fs = firestore_counts.get(name, 0)
        pg = postgres_counts.get(name, 0)
        missing = fs - pg
        total_missing += max(missing, 0)
        flag = "  <-- MISSING DATA" if missing > 0 else (" <-- unexpected: pg > firestore" if missing < 0 else "")
        print(f"{name:<45} {fs:>10} {pg:>10} {missing:>10}{flag}")

    print("-" * 78)
    if total_missing == 0:
        print("All stations fully synced.")
    elif total_missing <= MISSING_ROW_THRESHOLD:
        print(f"TOTAL MISSING: {total_missing} rows — within normal poll-lag threshold ({MISSING_ROW_THRESHOLD}), not alerting.")
    else:
        print(f"[ALERT] TOTAL MISSING: {total_missing} rows across the stations flagged above "
              f"(threshold {MISSING_ROW_THRESHOLD}).")
        print("checkpoint.json alone will not reveal this — see ingestion_checkpoint_desync_recurrence_2026-08-17 memory.")


if __name__ == "__main__":
    main()
