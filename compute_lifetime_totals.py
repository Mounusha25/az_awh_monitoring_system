"""
Maintain a running lifetime total of water actually harvested per station —
the number the Impact page shows — without streaming a station's entire
reading history on every page load. The active stations already have
125,000-500,000+ readings each; recomputing that live per request would take
minutes, not the sub-second response a landing page needs.

Uses the same filtered weight-delta-sum logic as the backend's hourly
aggregation (awh_az/backend/main.py, `_compute_hourly_aggregation_sync`):
deltas below WEIGHT_NOISE_FLOOR_G are dropped before summing, because some
stations' balance readings jitter +/-5-25g between readings with no real
accumulating trend (see the "Ignore weight-delta jitter" commit — naively
summing every positive wobble overcounted one station's 2-day production by
~50x). Keep this constant in sync with that function if it ever changes.

Persists progress into `stations/{name}/aggregates/lifetime_totals` in
Firestore, checkpointed by timestamp cursor (not offset — Firestore offset
pagination re-skips-and-discards every prior document, which gets
prohibitively slow past the first few pages). Every run only reads readings
that arrived since the last run, so the first run is the only expensive one;
after that it's incremental and cheap. Safe to re-run or interrupt at any
point — progress is checkpointed after every batch.

Usage:
    python3 compute_lifetime_totals.py
"""

import os
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials, firestore

FIREBASE_CREDENTIALS_PATH = os.getenv(
    "FIREBASE_CREDENTIALS_PATH",
    os.path.join(os.path.dirname(__file__), "awh_az/backend/awh-project-460421-52cd6ebf2aa3.json"),
)
WEIGHT_NOISE_FLOOR_G = 15  # must match awh_az/backend/main.py's hourly aggregation
BATCH_SIZE = 2000

_monitored_raw = os.getenv("MONITORED_STATIONS", "")
MONITORED_STATIONS = {s.strip() for s in _monitored_raw.split(",") if s.strip()} or None


def process_station(db, station_name: str) -> None:
    station_ref = db.collection("stations").document(station_name)
    agg_ref = station_ref.collection("aggregates").document("lifetime_totals")
    agg_doc = agg_ref.get()
    state = agg_doc.to_dict() if agg_doc.exists else {}

    total_g = state.get("total_water_g", 0.0)
    last_weight = state.get("last_weight_g")
    last_ts_str = state.get("last_timestamp")
    readings_processed = state.get("readings_processed", 0)
    new_docs_seen = 0

    while True:
        query = (
            station_ref.collection("readings")
            .select(["timestamp", "weight"])
            .order_by("timestamp", direction=firestore.Query.ASCENDING)
        )
        if last_ts_str:
            query = query.start_after({"timestamp": datetime.fromisoformat(last_ts_str)})

        batch = list(query.limit(BATCH_SIZE).stream())
        if not batch:
            break

        for doc in batch:
            data = doc.to_dict()
            weight = data.get("weight")
            ts = data.get("timestamp")
            if ts is not None:
                last_ts_str = ts.isoformat()
                readings_processed += 1
            if weight is None or ts is None:
                continue
            if last_weight is not None:
                delta = weight - last_weight
                if delta >= WEIGHT_NOISE_FLOOR_G:
                    total_g += delta
            last_weight = weight

        new_docs_seen += len(batch)

        agg_ref.set({
            "total_water_g": total_g,
            "last_weight_g": last_weight,
            "last_timestamp": last_ts_str,
            "readings_processed": readings_processed,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

        if len(batch) < BATCH_SIZE:
            break

    if new_docs_seen:
        print(f"{station_name}: +{new_docs_seen} new readings processed, "
              f"total so far {total_g / 1000:.2f} L ({readings_processed} readings lifetime)")
    else:
        print(f"{station_name}: up to date, {total_g / 1000:.2f} L ({readings_processed} readings lifetime)")


def main() -> None:
    cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    for sdoc in db.collection("stations").list_documents():
        if MONITORED_STATIONS is not None and sdoc.id not in MONITORED_STATIONS:
            continue
        process_station(db, sdoc.id)


if __name__ == "__main__":
    main()
