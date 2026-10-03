"""Tests for check_backend.py: the pure health/freshness evaluators and CLI wiring."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_backend as cb  # noqa: E402

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def station(name, minutes_ago=None):
    last = None if minutes_ago is None else (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
    return {"station_name": name, "metadata": {"last_reading": last}}


def test_health_ok():
    body = {"status": "healthy", "services": {"firestore": "online", "postgres": "unavailable (Firestore fallback active)"}}
    assert cb.evaluate_health(200, body) == []


def test_health_postgres_unavailable_is_not_a_failure():
    body = {"status": "healthy", "services": {"firestore": "online", "redis": "offline"}}
    assert cb.evaluate_health(200, body) == []


def test_health_firestore_offline_fails():
    body = {"status": "healthy", "services": {"firestore": "offline"}}
    assert any("Firestore" in p for p in cb.evaluate_health(200, body))


def test_health_non_200_fails():
    assert cb.evaluate_health(503, None) == ["/health returned HTTP 503"]


def test_freshness_recent_station_ok():
    assert cb.evaluate_freshness([station("a", 30)], {"a": 120}, NOW) == []


def test_freshness_silent_station_fails():
    problems = cb.evaluate_freshness([station("a", 300)], {"a": 120}, NOW)
    assert len(problems) == 1 and "silent for 300 min" in problems[0]


def test_freshness_missing_and_empty_stations_fail():
    problems = cb.evaluate_freshness([station("empty")], {"gone": 60, "empty": 60}, NOW)
    assert any("gone: not returned" in p for p in problems)
    assert any("empty: no readings" in p for p in problems)


def test_freshness_ignores_unwatched_stations():
    assert cb.evaluate_freshness([station("retired", 99999)], {}, NOW) == []


def test_freshness_naive_timestamp_treated_as_utc():
    s = {"station_name": "a", "metadata": {"last_reading": (NOW - timedelta(minutes=5)).replace(tzinfo=None).isoformat()}}
    assert cb.evaluate_freshness([s], {"a": 60}, NOW) == []


def test_main_health_pass_and_fail(monkeypatch, capsys):
    monkeypatch.setattr(cb, "fetch_json", lambda url, timeout=0: (200, {"status": "healthy", "services": {"firestore": "online"}}))
    assert cb.main(["--mode", "health"]) == 0
    monkeypatch.setattr(cb, "fetch_json", lambda url, timeout=0: (200, {"status": "healthy", "services": {"firestore": "offline"}}))
    assert cb.main(["--mode", "health"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_main_unreachable_backend_fails(monkeypatch):
    def boom(url, timeout=0):
        raise RuntimeError("unreachable")
    monkeypatch.setattr(cb, "fetch_json", boom)
    assert cb.main(["--mode", "health"]) == 1


def test_main_freshness_empty_watchlist_is_noop(tmp_path, monkeypatch):
    cfg = tmp_path / "s.json"
    cfg.write_text(json.dumps({"expected_live": {}}))
    monkeypatch.setattr(cb, "fetch_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")))
    assert cb.main(["--mode", "freshness", "--config", str(cfg)]) == 0


def test_main_freshness_detects_silent_station(tmp_path, monkeypatch):
    cfg = tmp_path / "s.json"
    cfg.write_text(json.dumps({"expected_live": {"a": 60}}))
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    monkeypatch.setattr(cb, "fetch_json", lambda url, timeout=0: (200, [{"station_name": "a", "metadata": {"last_reading": old}}]))
    assert cb.main(["--mode", "freshness", "--config", str(cfg)]) == 1


def test_repo_config_is_valid_json():
    cfg = json.loads(cb.CONFIG_PATH.read_text())
    assert isinstance(cfg["expected_live"], dict)
