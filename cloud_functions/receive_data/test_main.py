"""Tests for the receive_data Cloud Function, with Firestore faked."""
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask import Flask
from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore
from werkzeug.exceptions import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main  # noqa: E402

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


class FakeDocRef:
    def __init__(self, store, doc_id):
        self.store, self.doc_id = store, doc_id

    def create(self, data):
        if self.doc_id in self.store:
            raise AlreadyExists("exists")
        self.store[self.doc_id] = data

    def set(self, data):
        self.store[self.doc_id] = data


class FakeReadings:
    def __init__(self):
        self.store = {}
        self.auto = 0

    def document(self, doc_id=None):
        if doc_id is None:
            self.auto += 1
            doc_id = f"auto{self.auto}"
        return FakeDocRef(self.store, doc_id)


class RoutingDb:
    """collection('stations').document(name).collection('readings') -> one shared FakeReadings."""

    def __init__(self):
        self.readings = FakeReadings()
        self.station = None

    def collection(self, name):
        return self if name == "stations" else self.readings

    def document(self, name):
        self.station = name
        return self


@pytest.fixture
def db(monkeypatch):
    fake = RoutingDb()
    monkeypatch.setattr(main, "_db", fake)
    return fake


def call(payload=None, method="POST", raw=None, headers=None):
    app = Flask(__name__)
    kwargs = {"method": method, "headers": headers or {}}
    if payload is not None:
        kwargs["json"] = payload
    if raw is not None:
        kwargs.update(data=raw, content_type="application/json")
    with app.test_request_context(**kwargs):
        from flask import request
        resp = main.receive_data(request)
    if isinstance(resp, tuple):
        resp, status = resp
        return status, resp.get_json()
    return resp.status_code, resp.get_json()


def only_doc(db):
    (doc,) = db.readings.store.values()
    return doc


# ---------------------------------------------------------------------------
# Backward-compatible behaviour (old stations send neither control field)
# ---------------------------------------------------------------------------
def test_legacy_payload_stored_with_server_timestamp(db):
    status, body = call({"station_name": "s1", "temperature": 21.5})
    assert status == 200 and body["status"] == "success" and body["timestamp_source"] == "server"
    doc = only_doc(db)
    assert doc["temperature"] == 21.5 and doc["timestamp"] is firestore.SERVER_TIMESTAMP
    assert list(db.readings.store) == ["auto1"]  # auto id, as before


def test_client_timestamp_ignored_when_not_replayed(db):
    call({"station_name": "s1", "client_timestamp": "2026-10-03T08:00:00Z"})
    assert only_doc(db)["timestamp"] is firestore.SERVER_TIMESTAMP


def test_control_fields_not_stored(db):
    call({"station_name": "s1", "reading_id": "abcdefgh1234", "replayed": True,
          "client_timestamp": "2026-10-03T08:00:00Z", "weight": 5})
    doc = only_doc(db)
    assert set(doc) == {"station_name", "weight", "timestamp"}


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
def test_reading_id_becomes_document_id(db):
    call({"station_name": "s1", "reading_id": "abcdefgh1234"})
    assert list(db.readings.store) == ["abcdefgh1234"]


def test_duplicate_reading_id_is_success_not_second_doc(db):
    call({"station_name": "s1", "reading_id": "abcdefgh1234", "weight": 1})
    status, body = call({"station_name": "s1", "reading_id": "abcdefgh1234", "weight": 999})
    assert status == 200 and body["status"] == "duplicate"
    assert len(db.readings.store) == 1 and only_doc(db)["weight"] == 1  # first write kept


@pytest.mark.parametrize("bad", ["short", "has space!!", "x" * 65, 12345678, ""])
def test_invalid_reading_id_rejected(db, bad):
    with pytest.raises(HTTPException) as exc:
        call({"station_name": "s1", "reading_id": bad})
    assert exc.value.code == 400
    assert db.readings.store == {}


# ---------------------------------------------------------------------------
# Timestamp resolution
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ts,expected_source", [
    ("2026-10-03T08:00:00Z", "client"),             # 4h ago
    ("2026-10-03T08:00:00+00:00", "client"),
    ("2026-10-03T01:00:00-07:00", "client"),        # offset converted to UTC (08:00Z)
    ("2026-10-03T08:00:00", "client"),              # naive -> assumed UTC
    ("2026-09-03T12:00:00Z", "client"),             # exactly 30 days: boundary allowed
    ("2026-09-03T11:59:00Z", "server"),             # older than 30 days
    ("2026-10-03T12:04:00Z", "client"),             # 4 min future: clock skew allowed
    ("2026-10-03T12:06:00Z", "server"),             # too far in the future
    ("not a date", "server"),
    (None, "server"),
    (12345, "server"),
])
def test_resolve_timestamp_window(ts, expected_source):
    value, source = main.resolve_timestamp(ts, True, NOW)
    assert source == expected_source
    if source == "client":
        assert isinstance(value, datetime) and value.tzinfo is not None
    else:
        assert value is firestore.SERVER_TIMESTAMP


def test_resolve_timestamp_requires_replayed_true():
    for flag in (False, None, "true", 1):
        assert main.resolve_timestamp("2026-10-03T08:00:00Z", flag, NOW)[1] == "server"


def test_replayed_reading_stored_with_client_timestamp(db):
    iso = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    status, body = call({"station_name": "s1", "replayed": True, "client_timestamp": iso, "reading_id": "abcdefgh1234"})
    assert status == 200 and body["timestamp_source"] == "client"
    assert only_doc(db)["timestamp"] == datetime.fromisoformat(iso)


# ---------------------------------------------------------------------------
# Request validation (these used to come back as 500 - see main.py)
# ---------------------------------------------------------------------------
def test_non_post_is_405(db):
    with pytest.raises(HTTPException) as exc:
        call(method="GET")
    assert exc.value.code == 405


@pytest.mark.parametrize("payload", [{}, {"temperature": 1}, {"station_name": ""}, {"station_name": 5}])
def test_bad_station_name_is_400(db, payload):
    with pytest.raises(HTTPException) as exc:
        call(payload if payload else None, raw="{}" if not payload else None)
    assert exc.value.code == 400


def test_firestore_failure_is_500_without_leaking_details(db, monkeypatch):
    def boom(self, data):
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr(FakeDocRef, "set", boom)
    status, body = call({"station_name": "s1"})
    assert status == 500 and body["message"] == "Internal error"
    assert "secret" not in str(body)


# ---------------------------------------------------------------------------
# Authentication: shared key in X-Station-Key, soft mode then enforce mode
# ---------------------------------------------------------------------------
KEY = "k-ASU-1234567890abcdef"
OTHER = "k-SRP-fedcba0987654321"
OK = {"station_name": "s1", "weight": 1}


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("STATION_KEYS", raising=False)
    monkeypatch.delenv("REQUIRE_STATION_KEY", raising=False)
    return monkeypatch


def rejected(db, payload, headers=None, code=401):
    with pytest.raises(HTTPException) as exc:
        call(payload, headers=headers)
    assert exc.value.code == code
    assert db.readings.store == {}, "a rejected request must not write anything"


def test_auth_off_when_no_keys_configured(db, clean_env):
    assert call(OK)[0] == 200                                   # legacy behaviour
    assert call(dict(OK), headers={"X-Station-Key": "anything"})[0] == 200


def test_soft_mode_accepts_missing_key_but_logs_the_station(db, clean_env, caplog):
    clean_env.setenv("STATION_KEYS", KEY)
    caplog.set_level(logging.WARNING, logger="receive_data")
    assert call({"station_name": "station_old_pi", "weight": 1})[0] == 200
    assert "unauthenticated request" in caplog.text and "station_old_pi" in caplog.text


def test_soft_mode_valid_key_is_quiet(db, clean_env, caplog):
    clean_env.setenv("STATION_KEYS", KEY)
    caplog.set_level(logging.WARNING, logger="receive_data")
    assert call(OK, headers={"X-Station-Key": KEY})[0] == 200
    assert "unauthenticated" not in caplog.text


def test_soft_mode_wrong_key_is_always_rejected(db, clean_env):
    clean_env.setenv("STATION_KEYS", KEY)
    rejected(db, OK, {"X-Station-Key": "wrong-key-value"})
    rejected(db, OK, {"X-Station-Key": KEY + "x"})             # near miss
    rejected(db, OK, {"X-Station-Key": KEY[:-1]})


def test_enforce_mode_requires_a_valid_key(db, clean_env):
    clean_env.setenv("STATION_KEYS", KEY)
    clean_env.setenv("REQUIRE_STATION_KEY", "true")
    rejected(db, OK)                                            # missing
    rejected(db, OK, {"X-Station-Key": "nope"})                 # wrong
    assert call(dict(OK), headers={"X-Station-Key": KEY})[0] == 200


@pytest.mark.parametrize("flag", ["TRUE", " true ", "True"])
def test_enforce_flag_parsing(db, clean_env, flag):
    clean_env.setenv("STATION_KEYS", KEY)
    clean_env.setenv("REQUIRE_STATION_KEY", flag)
    rejected(db, OK)


@pytest.mark.parametrize("flag", ["false", "0", "", "yes"])
def test_only_true_enforces(db, clean_env, flag):
    clean_env.setenv("STATION_KEYS", KEY)
    clean_env.setenv("REQUIRE_STATION_KEY", flag)
    assert call(OK)[0] == 200


def test_enforce_without_any_keys_fails_closed(db, clean_env, caplog):
    clean_env.setenv("REQUIRE_STATION_KEY", "true")
    caplog.set_level(logging.ERROR, logger="receive_data")
    rejected(db, OK)
    rejected(db, OK, {"X-Station-Key": "whatever"})
    assert "STATION_KEYS is empty" in caplog.text


def test_multiple_keys_allow_rotation(db, clean_env):
    clean_env.setenv("STATION_KEYS", f" {KEY} , {OTHER} ,, ")   # stray spaces/commas tolerated
    clean_env.setenv("REQUIRE_STATION_KEY", "true")
    assert call(dict(OK), headers={"X-Station-Key": KEY})[0] == 200
    assert call(dict(OK), headers={"X-Station-Key": OTHER})[0] == 200
    assert len(db.readings.store) == 2
    db.readings.store.clear()
    rejected(db, OK, {"X-Station-Key": "retired-key-0000"})


def test_non_ascii_key_header_is_rejected_cleanly(db, clean_env):
    clean_env.setenv("STATION_KEYS", KEY)
    rejected(db, OK, {"X-Station-Key": "k\u00e9y-with-accent"})


def test_keys_are_never_logged_or_returned(db, clean_env, caplog):
    clean_env.setenv("STATION_KEYS", KEY)
    caplog.set_level(logging.DEBUG)
    call(dict(OK), headers={"X-Station-Key": KEY})
    with pytest.raises(HTTPException) as exc:
        call(dict(OK), headers={"X-Station-Key": "guess-guess-guess"})
    assert KEY not in caplog.text and "guess-guess-guess" not in caplog.text
    assert KEY not in str(exc.value.description)


def test_auth_checked_before_body_is_parsed(db, clean_env):
    clean_env.setenv("STATION_KEYS", KEY)
    clean_env.setenv("REQUIRE_STATION_KEY", "true")
    with pytest.raises(HTTPException) as exc:
        call(raw="{not json")
    assert exc.value.code == 401


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", [
    "a/b", "x/readings/y", "..", ".", "__reserved__", "a" * 201, "bad\nname", "tab\tname", "nul\x00",
])
def test_unsafe_station_names_rejected(db, clean_env, name):
    rejected(db, {"station_name": name, "weight": 1}, code=400)


@pytest.mark.parametrize("name", [
    "station_AquaPars #2 @Power Station, Tempe",   # real names contain spaces, #, @ and commas
    "station_testbed_1@Powerplant",
    "station_Dewstand @ GreenHouse, Polytech",
    "a" * 200,
])
def test_real_station_names_accepted(db, clean_env, name):
    assert call({"station_name": name, "weight": 1})[0] == 200


def test_oversized_body_rejected(db, clean_env):
    big = {"station_name": "s1", "blob": "x" * (main.MAX_BODY_BYTES + 10)}
    rejected(db, big, code=413)
