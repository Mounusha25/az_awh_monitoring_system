"""receive_data — HTTP Cloud Function: station (Raspberry Pi) -> Firestore.

Deployed in GCP project awh-project-460421 as `receive_data` and called by
RPi_USB_Package/AquaPars1.py and AquaPars1_new_pm.py. Source lives here so it
is version-controlled and tested; deploying it is a manual step (see README.md).

Payload: JSON with `station_name` plus the sensor fields. Stored under
stations/{station_name}/readings.

Optional control fields (backward compatible - old stations omit them):
  reading_id        unique id chosen by the station (8-64 chars of A-Z a-z 0-9 _ -).
                    Used as the Firestore document id, so a retry of the same reading
                    is a no-op instead of a duplicate.
  replayed          true when the station is re-sending a reading it queued during an
                    outage. Only then is client_timestamp honoured.
  client_timestamp  ISO-8601 time the reading was captured. Honoured only for replayed
                    readings that fall within [now - 30 days, now + 5 min]; otherwise
                    the server time is used. Live uploads always get the server time.

The control fields are removed before storing, so they never appear as sensor
fields downstream (the backend lists every stored key as an available field).
"""
import re
from datetime import datetime, timedelta, timezone

import functions_framework
from flask import jsonify, abort, Request
from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore
from werkzeug.exceptions import HTTPException

MAX_REPLAY_AGE = timedelta(days=30)
MAX_FUTURE_SKEW = timedelta(minutes=5)
READING_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

_db = None


def get_db():
    """Created lazily so importing this module (tests) needs no credentials."""
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


def resolve_timestamp(client_timestamp, replayed: bool, now: datetime):
    """Return (timestamp_value, source) where source is 'server' or 'client'."""
    if replayed is True and isinstance(client_timestamp, str):
        try:
            ts = datetime.fromisoformat(client_timestamp.replace("Z", "+00:00"))
        except ValueError:
            return firestore.SERVER_TIMESTAMP, "server"
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts = ts.astimezone(timezone.utc)
        if now - MAX_REPLAY_AGE <= ts <= now + MAX_FUTURE_SKEW:
            return ts, "client"
    return firestore.SERVER_TIMESTAMP, "server"


@functions_framework.http
def receive_data(request: Request):
    if request.method != "POST":
        return abort(405)

    try:
        data = request.get_json()
        if not data:
            return abort(400, "No JSON received.")

        station_name = data.get("station_name")
        if not station_name or not isinstance(station_name, str):
            return abort(400, "Missing or invalid station_name in payload.")

        reading_id = data.pop("reading_id", None)
        if reading_id is not None and not (isinstance(reading_id, str) and READING_ID_RE.match(reading_id)):
            return abort(400, "Invalid reading_id.")
        replayed = data.pop("replayed", False)
        client_timestamp = data.pop("client_timestamp", None)

        data["timestamp"], source = resolve_timestamp(client_timestamp, replayed, datetime.now(timezone.utc))

        readings = get_db().collection("stations").document(station_name).collection("readings")
        if reading_id:
            try:
                readings.document(reading_id).create(data)
            except AlreadyExists:
                return jsonify({"status": "duplicate", "message": f"Reading already stored for {station_name}"}), 200
        else:
            readings.document().set(data)

        return jsonify({"status": "success", "message": f"Data stored for station {station_name}",
                        "timestamp_source": source}), 200

    except HTTPException:
        raise  # abort(400/405) above must stay 4xx; the old code turned them into 500s
    except Exception:
        import traceback
        print(traceback.format_exc())
        return jsonify({"status": "error", "message": "Internal error"}), 500
