"""Tests for logging_config: request ids, access lines, 500 handling, secrets, setup."""
import json
import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import logging_config as lc
from logging_config import RequestLoggingMiddleware, request_id_var


def request_records(caplog):
    return [r for r in caplog.records if r.name == "awh.request"]


@pytest.fixture
def mini_client():
    """A tiny app wrapped in the middleware, so error paths don't touch main.app."""
    app = FastAPI()

    @app.get("/ok")
    def ok():
        return {"rid": request_id_var.get()}

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    @app.get("/items/{station_name}")
    def item(station_name: str):
        return {"s": station_name}

    app.add_middleware(RequestLoggingMiddleware)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Request ids
# ---------------------------------------------------------------------------
def test_generated_request_id_in_header_and_handler(mini_client):
    r = mini_client.get("/ok")
    rid = r.headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{12}", rid)
    assert r.json()["rid"] == rid  # contextvar visible inside the handler


def test_valid_incoming_request_id_is_kept(mini_client):
    r = mini_client.get("/ok", headers={"X-Request-ID": "trace-abc.123"})
    assert r.headers["x-request-id"] == "trace-abc.123"


@pytest.mark.parametrize("bad", ["has space", "x" * 65, "semi;colon", "new\tline"])
def test_invalid_incoming_request_id_is_replaced(mini_client, bad):
    r = mini_client.get("/ok", headers={"X-Request-ID": bad})
    assert r.headers["x-request-id"] != bad
    assert re.fullmatch(r"[0-9a-f]{12}", r.headers["x-request-id"])


def test_request_id_resets_between_requests(mini_client):
    mini_client.get("/ok")
    assert request_id_var.get() == "-"


# ---------------------------------------------------------------------------
# Access line
# ---------------------------------------------------------------------------
def test_access_line_fields_and_station(mini_client, caplog):
    caplog.set_level(logging.DEBUG, logger="awh.request")
    mini_client.get("/items/st1@Lab")
    (rec,) = request_records(caplog)
    assert (rec.method, rec.path, rec.status, rec.station) == ("GET", "/items/st1@Lab", 200, "st1@Lab")
    assert rec.levelno == logging.INFO
    assert isinstance(rec.duration_ms, float) and rec.slow is False


def test_4xx_is_warning_and_5xx_is_error(client, mini_client, caplog):
    caplog.set_level(logging.DEBUG, logger="awh.request")
    client.get("/stations/nope/readings")  # no Firestore in test -> 503
    assert request_records(caplog)[-1].levelno == logging.ERROR
    mini_client.get("/missing")  # 404
    assert request_records(caplog)[-1].levelno == logging.WARNING


def test_health_is_quiet_at_info(client, caplog):
    caplog.set_level(logging.INFO, logger="awh.request")
    client.get("/health")
    assert request_records(caplog) == []
    caplog.set_level(logging.DEBUG, logger="awh.request")
    client.get("/health")
    assert request_records(caplog)[-1].levelno == logging.DEBUG


def test_real_app_sets_request_id_header(client):
    assert "x-request-id" in client.get("/").headers


def test_slow_request_is_flagged(mini_client, caplog, monkeypatch):
    monkeypatch.setattr(lc, "SLOW_REQUEST_SECONDS", -1.0)
    caplog.set_level(logging.DEBUG, logger="awh.request")
    mini_client.get("/ok")
    rec = request_records(caplog)[-1]
    assert rec.slow is True and rec.levelno == logging.WARNING


# ---------------------------------------------------------------------------
# Unhandled exceptions
# ---------------------------------------------------------------------------
def test_unhandled_exception_returns_traceable_500(mini_client, caplog):
    caplog.set_level(logging.DEBUG, logger="awh.request")
    r = mini_client.get("/boom")
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal Server Error", "request_id": r.headers["x-request-id"]}
    recs = request_records(caplog)
    assert len(recs) == 1  # one error record, not a duplicate access line
    assert recs[0].levelno == logging.ERROR and recs[0].exc_info
    assert "kaboom" in str(recs[0].exc_info[1])


# ---------------------------------------------------------------------------
# Secrets never reach the logs
# ---------------------------------------------------------------------------
def test_headers_and_query_strings_are_not_logged(client, caplog):
    caplog.set_level(logging.DEBUG)
    client.get("/admin/stations?token=QUERYSECRET", headers={"X-Admin-Key": "HEADERSECRET"})
    dumped = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "HEADERSECRET" not in dumped
    assert "QUERYSECRET" not in dumped


# ---------------------------------------------------------------------------
# Formatter and setup
# ---------------------------------------------------------------------------
def _record(**extra):
    rec = logging.LogRecord("awh.request", logging.INFO, __file__, 1, "request %s", ("x",), None)
    rec.request_id = "abc123"
    rec.__dict__.update(extra)
    return rec


def test_json_formatter_output():
    line = lc.JsonFormatter().format(_record(method="GET", path="/s", status=200, duration_ms=3.2, station="a"))
    d = json.loads(line)
    assert d["message"] == "request x" and d["level"] == "INFO" and d["request_id"] == "abc123"
    assert (d["method"], d["status"], d["station"], d["duration_ms"]) == ("GET", 200, "a", 3.2)
    assert d["ts"].endswith("+00:00")


def test_json_formatter_includes_exception():
    try:
        raise ValueError("bad")
    except ValueError:
        import sys
        rec = _record()
        rec.exc_info = sys.exc_info()
    d = json.loads(lc.JsonFormatter().format(rec))
    assert "ValueError: bad" in d["exception"]


def test_json_formatter_ignores_unlisted_extras():
    d = json.loads(lc.JsonFormatter().format(_record(authorization="Bearer SECRET")))
    assert "authorization" not in d and "SECRET" not in json.dumps(d)


def test_format_selection(monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    assert lc._use_json() is False
    monkeypatch.setenv("RENDER", "true")
    assert lc._use_json() is True
    monkeypatch.setenv("LOG_FORMAT", "text")  # explicit setting beats auto-detect
    assert lc._use_json() is False


def test_setup_is_idempotent_and_honours_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "warning")
    lc.setup_logging()
    lc.setup_logging()
    root = logging.getLogger()
    ours = [h for h in root.handlers if getattr(h, lc._HANDLER_MARK, False)]
    assert len(ours) == 1
    assert root.level == logging.WARNING
    assert logging.getLogger("uvicorn.access").propagate is False
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    lc.setup_logging()
