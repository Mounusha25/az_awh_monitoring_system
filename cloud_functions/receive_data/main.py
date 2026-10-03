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

Authentication (shared secret in the X-Station-Key header)
  STATION_KEYS          comma-separated valid keys (several allows zero-downtime rotation).
  REQUIRE_STATION_KEY   "true" to enforce. Anything else = soft mode.
  Soft mode (default): a request with NO key is still accepted but logged with its
  station name ("unauthenticated request"), so you can see which stations still need
  the key; a request with a WRONG key is always rejected (401). If STATION_KEYS is
  empty and enforcement is off, auth is off entirely (legacy behaviour).
  Enforce mode: missing or wrong key -> 401. With no keys configured it rejects
  everything (fail closed) rather than silently opening up.
Keys are never logged or echoed back.
"""
import hmac
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import functions_framework
from flask import jsonify, abort, Request
from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore
from werkzeug.exceptions import HTTPException

KEY_HEADER = "X-Station-Key"
MAX_BODY_BYTES = 64 * 1024
MAX_STATION_NAME_LEN = 200

logger = logging.getLogger("receive_data")

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


def configured_keys():
    return [k.strip() for k in os.environ.get("STATION_KEYS", "").split(",") if k.strip()]


def enforcing():
    return os.environ.get("REQUIRE_STATION_KEY", "").strip().lower() == "true"


def check_auth(headers):
    """Return (allowed, missing_key). missing_key=True means accepted in soft mode without a key."""
    keys = configured_keys()
    provided = headers.get(KEY_HEADER)
    if enforcing() and not keys:
        logger.error("REQUIRE_STATION_KEY is set but STATION_KEYS is empty; rejecting everything")
        return False, False
    if provided:
        ok = any(hmac.compare_digest(provided.encode("utf-8"), k.encode("utf-8")) for k in keys)
        if keys and not ok:
            return False, False
        return True, False          # valid key (or auth off and a key was sent: ignore it)
    if enforcing():
        return False, False
    return True, bool(keys)         # soft mode: allowed, flagged only if keys exist to migrate to


def valid_station_name(name) -> bool:
    """Firestore document id rules: no '/', not '.'/'..', not __x__, bounded; no control chars."""
    return (isinstance(name, str) and 0 < len(name) <= MAX_STATION_NAME_LEN
            and "/" not in name and name not in (".", "..")
            and not re.fullmatch(r"__.*__", name)
            and not any(ord(c) < 32 or ord(c) == 127 for c in name))


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

    allowed, missing_key = check_auth(request.headers)
    if not allowed:
        return abort(401)
    if (request.content_length or 0) > MAX_BODY_BYTES:
        return abort(413)

    try:
        data = request.get_json()
        if not data:
            return abort(400, "No JSON received.")

        station_name = data.get("station_name")
        if not station_name or not valid_station_name(station_name):
            return abort(400, "Missing or invalid station_name in payload.")
        if missing_key:
            logger.warning("unauthenticated request (no %s) from station %r", KEY_HEADER, station_name)

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
