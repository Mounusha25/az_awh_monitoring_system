"""API tests for the FastAPI backend, with Firestore and Postgres faked.

Run from the repo root:  pytest awh_az/backend/tests -v
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import main
from conftest import FakePool, reading


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["docs"] == "/docs"


def test_health_without_backends(client):
    body = client.get("/health").json()
    assert body["status"] == "healthy"
    assert body["services"]["firestore"] == "offline"
    # No Postgres is a supported state (Firestore fallback), not a failure.
    assert body["services"]["postgres"].startswith("unavailable")


def test_health_with_firestore(client, use_firestore):
    use_firestore()
    assert client.get("/health").json()["services"]["firestore"] == "online"


# ---------------------------------------------------------------------------
# /stations
# ---------------------------------------------------------------------------
def test_stations_503_without_firestore(client):
    assert client.get("/stations").status_code == 503


def test_stations_status_and_fields(client, use_firestore):
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    use_firestore({
        "live@Lab": {
            "readings": [reading(temperature=25.0, weight=10.0)],
            "meta": {"display_name": "Live Lab"},
        },
        "dead@Lab": {"readings": [reading(ts=old, temperature=20.0)]},
        "empty@Lab": {"readings": []},  # no readings -> omitted from the list
    })
    r = client.get("/stations")
    assert r.status_code == 200
    by_name = {s["station_name"]: s for s in r.json()}

    assert set(by_name) == {"live@Lab", "dead@Lab"}
    assert by_name["live@Lab"]["status"] == "active"
    assert by_name["live@Lab"]["display_name"] == "Live Lab"
    assert by_name["dead@Lab"]["status"] == "inactive"
    assert by_name["live@Lab"]["metadata"]["available_fields"] == ["temperature", "unit", "weight"]
    assert "Intake Air" in by_name["live@Lab"]["metadata"]["field_groups"]


def test_stations_response_is_cached(client, use_firestore):
    use_firestore({"a@Lab": {"readings": [reading(temperature=1.0)]}})
    first = client.get("/stations").json()
    # Swap the backing data: a cached response must not notice.
    use_firestore({"b@Lab": {"readings": [reading(temperature=2.0)]}})
    assert client.get("/stations").json() == first


# ---------------------------------------------------------------------------
# /stations/{name}/readings
# ---------------------------------------------------------------------------
def test_readings_503_without_firestore(client):
    assert client.get("/stations/x/readings").status_code == 503


def test_readings_limit_over_max_is_rejected(client, use_firestore):
    use_firestore({"x": {"readings": [reading()]}})
    assert client.get("/stations/x/readings?limit=10001").status_code == 422


def test_readings_firestore_fallback(client, use_firestore):
    use_firestore({"x": {"readings": [reading(temperature=21.5), reading(temperature=22.5)]}})
    r = client.get("/stations/x/readings?limit=1")
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["temperature"] == 21.5
    assert body["limit"] == 1 and body["offset"] == 0


def test_readings_unknown_station_404(client, use_firestore):
    use_firestore({})
    assert client.get("/stations/nope/readings").status_code == 404


def test_readings_fields_filter(client, use_firestore):
    use_firestore({"x": {"readings": [reading(temperature=21.5, humidity=40.0, weight=3.0)]}})
    row = client.get("/stations/x/readings?fields=temperature").json()["data"][0]
    assert row["temperature"] == 21.5
    assert row["humidity"] is None and row["weight"] is None  # filtered out


def test_readings_postgres_path(client, use_firestore, monkeypatch):
    """With a pool present, rows come from Postgres and 'time' maps to 'timestamp'."""
    use_firestore({})
    monkeypatch.setattr(main, "db_pool", FakePool())
    seen = {}

    def fake_fetch(station, start, end, limit, offset, ascending):
        seen.update(station=station, limit=limit, offset=offset, ascending=ascending)
        return [{"time": datetime(2026, 1, 1, tzinfo=timezone.utc), "temperature": 30.0}]

    monkeypatch.setattr(main, "_fetch_readings_rows", fake_fetch)
    r = client.get("/stations/pg@Lab/readings?limit=5&offset=2")
    assert r.status_code == 200
    assert r.json()["data"][0]["temperature"] == 30.0
    assert r.json()["total"] == 3  # offset + rows returned
    # No start_date -> newest first.
    assert seen == {"station": "pg@Lab", "limit": 5, "offset": 2, "ascending": False}


def test_readings_start_date_orders_ascending(client, use_firestore, monkeypatch):
    use_firestore({})
    monkeypatch.setattr(main, "db_pool", FakePool())
    seen = {}

    def fake_fetch(station, start, end, limit, offset, ascending):
        seen["ascending"] = ascending
        return [{"time": datetime(2026, 1, 1, tzinfo=timezone.utc)}]

    monkeypatch.setattr(main, "_fetch_readings_rows", fake_fetch)
    assert client.get("/stations/pg@Lab/readings?start_date=2026-01-01T00:00:00Z").status_code == 200
    assert seen["ascending"] is True


def test_readings_postgres_no_rows_404(client, use_firestore, monkeypatch):
    use_firestore({})
    monkeypatch.setattr(main, "db_pool", FakePool())
    monkeypatch.setattr(main, "_fetch_readings_rows", lambda *a, **k: None)
    assert client.get("/stations/pg@Lab/readings").status_code == 404


# ---------------------------------------------------------------------------
# /impact
# ---------------------------------------------------------------------------
def test_impact_503_without_firestore(client):
    assert client.get("/impact").status_code == 503


def test_impact_sums_stations_and_skips_missing_totals(client, use_firestore):
    use_firestore({
        "a@Powerplant": {"totals": {"total_water_g": 2000.0, "readings_processed": 10}},
        "b@Lab": {"totals": {"total_water_g": 500.0, "readings_processed": 4}},
        "c@Lab": {"totals": None},  # job hasn't run for this station yet
    })
    body = client.get("/impact").json()
    assert body["total_liters"] == 2.5
    assert [s["station_name"] for s in body["stations"]] == ["a@Powerplant", "b@Lab"]  # sorted desc
    assert body["stations"][0]["location"] == "Powerplant"


# ---------------------------------------------------------------------------
# Admin auth — fails closed
# ---------------------------------------------------------------------------
def test_admin_rejects_missing_key(client, monkeypatch):
    monkeypatch.setattr(main.settings, "admin_api_key", "secret")
    assert client.get("/admin/stations").status_code == 403


def test_admin_rejects_wrong_key(client, monkeypatch):
    monkeypatch.setattr(main.settings, "admin_api_key", "secret")
    assert client.get("/admin/stations", headers={"X-Admin-Key": "nope"}).status_code == 403


def test_admin_fails_closed_when_key_unconfigured(client):
    # ADMIN_API_KEY unset: even a plausible header must not get in.
    assert client.get("/admin/stations", headers={"X-Admin-Key": ""}).status_code == 403
    assert client.get("/admin/stations", headers={"X-Admin-Key": "anything"}).status_code == 403


def test_admin_accepts_correct_key(client, monkeypatch):
    monkeypatch.setattr(main.settings, "admin_api_key", "secret")
    # Authenticated, so it gets past the gate and hits the 'no Firestore' check.
    assert client.get("/admin/stations", headers={"X-Admin-Key": "secret"}).status_code == 503


def test_require_admin_key_direct():
    main.settings.admin_api_key = ""
    with pytest.raises(HTTPException) as exc:
        main.require_admin_key("x")
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_excluded_window_only_for_listed_station():
    assert main._is_excluded_ts("station_testbed_1@Powerplant", "2026-08-20T12:00:00+00:00")
    assert not main._is_excluded_ts("station_testbed_1@Powerplant", "2026-09-11T00:00:00+00:00")  # end exclusive
    assert not main._is_excluded_ts("station_testbed_1@Powerplant", "2026-08-07T23:59:59+00:00")
    assert not main._is_excluded_ts("other@Lab", "2026-08-20T12:00:00+00:00")
    assert not main._is_excluded_ts("station_testbed_1@Powerplant", None)


def test_split_range_no_overlap_is_unchanged():
    win = [(utc(2026, 8, 8), utc(2026, 9, 11))]
    assert main._split_range_around_windows(utc(2026, 1, 1), utc(2026, 2, 1), win) == [
        (utc(2026, 1, 1), utc(2026, 2, 1), True)
    ]


def test_split_range_straddling_window_is_cut_in_two():
    win = [(utc(2026, 8, 8), utc(2026, 9, 11))]
    segs = main._split_range_around_windows(utc(2026, 8, 1), utc(2026, 9, 20), win)
    assert segs == [
        (utc(2026, 8, 1), utc(2026, 8, 8), False),   # stops short of the window
        (utc(2026, 9, 11), utc(2026, 9, 20), True),  # resumes after it
    ]


def test_split_range_open_ended_covers_both_sides():
    win = [(utc(2026, 8, 8), utc(2026, 9, 11))]
    assert main._split_range_around_windows(None, None, win) == [
        (None, utc(2026, 8, 8), False),
        (utc(2026, 9, 11), None, True),
    ]


def test_split_range_fully_inside_window_is_empty():
    win = [(utc(2026, 8, 8), utc(2026, 9, 11))]
    assert main._split_range_around_windows(utc(2026, 8, 10), utc(2026, 8, 20), win) == []


def test_absolute_humidity_known_value():
    # 25 C / 50% RH is ~11.5 g/m3.
    assert main._compute_absolute_humidity(25.0, 50.0) == pytest.approx(11.5, abs=0.1)
    assert main._compute_absolute_humidity(25.0, 0.0) == 0.0


@pytest.mark.parametrize("value,unit,expected", [
    (3.6, "km/h", 1.0),
    (2.23694, "mph", 1.0),
    (3.28084, "ft/s", 1.0),
    (5.0, "m/s", 5.0),
    (5.0, None, 5.0),
])
def test_velocity_to_mps(value, unit, expected):
    assert main._velocity_to_mps(value, unit) == pytest.approx(expected)


def test_build_field_groups_ignores_unknown_fields():
    groups = main._build_field_groups(["temperature", "weight", "mystery"])
    assert groups == {"Intake Air": ["temperature"], "Water Production": ["weight"]}


def test_location_label_normalised():
    assert main._normalize_location_label(" PowerPlant ") == main._normalize_location_label("powerplant")
