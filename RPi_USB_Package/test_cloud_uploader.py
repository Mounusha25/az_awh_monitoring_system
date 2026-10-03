"""Tests for cloud_uploader.py. Pure software: no sensors, no network (post is injected)."""
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cloud_uploader as cu  # noqa: E402


class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


class FakePost:
    """Scripted responses: ints become Resp(status), Exceptions are raised, then default."""

    def __init__(self, script=(), default=200):
        self.script, self.default, self.calls = list(script), default, []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append(json)
        step = self.script.pop(0) if self.script else self.default
        if isinstance(step, Exception):
            raise step
        return Resp(step)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(tmp_path, post=None, synced=True, clock=None, **kw):
    clock = clock or Clock()
    post = post or FakePost()
    up = cu.CloudUploader("http://x/receive", str(tmp_path / "q.sqlite3"), post=post,
                          clock_synced=lambda: synced, time_fn=clock, **kw)
    return up, post, clock


# ---------------------------------------------------------------------------
# Live path
# ---------------------------------------------------------------------------
def test_live_reading_sent_without_timestamp_fields(tmp_path):
    up, post, _ = make(tmp_path)
    assert up.submit("st1", {"weight": 5.0, "power": None})
    assert up.attempt_next() == "sent"
    (body,) = post.calls
    assert body["station_name"] == "st1" and body["weight"] == 5.0 and body["power"] is None
    assert len(body["reading_id"]) == 32
    assert "replayed" not in body and "client_timestamp" not in body
    assert len(up.queue) == 0 and up.attempt_next() == "empty"


def test_submit_is_nonblocking_and_never_raises(tmp_path):
    up, _, _ = make(tmp_path)
    up.queue.close()  # break the DB: submit must swallow it, not crash the save loop
    assert up.submit("st1", {"a": 1}) is False


# ---------------------------------------------------------------------------
# Outage -> backlog -> recovery
# ---------------------------------------------------------------------------
def test_failure_keeps_reading_and_recovery_drains_in_order(tmp_path):
    up, post, clock = make(tmp_path, post=FakePost([requests_error()] * 3))
    for i in range(3):
        up.submit("st1", {"n": i})
        clock.t += 60
        assert up.attempt_next() == "retry"      # outage: nothing is lost
    assert len(up.queue) == 3 and up.stats()["failing"] and "ConnectionError" in up.stats()["last_error"]

    post.script.clear()  # network back
    order = []
    while up.attempt_next() == "sent":
        order.append(post.calls[-1]["n"])
    assert order == [0, 1, 2]                    # oldest first
    assert len(up.queue) == 0 and not up.stats()["failing"] and up.stats()["last_error"] is None


def requests_error():
    import requests
    return requests.exceptions.ConnectionError("network down")


@pytest.mark.parametrize("status", [500, 502, 503, 408, 429])
def test_retryable_http_statuses_keep_the_reading(tmp_path, status):
    up, _, _ = make(tmp_path, post=FakePost([status]))
    up.submit("st1", {"n": 1})
    assert up.attempt_next() == "retry" and len(up.queue) == 1


@pytest.mark.parametrize("status", [400, 404, 405, 413])
def test_permanent_4xx_drops_the_reading(tmp_path, status):
    up, _, _ = make(tmp_path, post=FakePost([status]))
    up.submit("st1", {"n": 1})
    assert up.attempt_next() == "dropped" and len(up.queue) == 0  # would never succeed; don't block the queue


# ---------------------------------------------------------------------------
# Idempotency: a retry reuses the same reading_id
# ---------------------------------------------------------------------------
def test_retry_reuses_same_reading_id(tmp_path):
    up, post, _ = make(tmp_path, post=FakePost([TimeoutError("read timed out")]))
    up.submit("st1", {"n": 1})
    assert up.attempt_next() == "retry"
    assert up.attempt_next() == "sent"
    assert post.calls[0]["reading_id"] == post.calls[1]["reading_id"]


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------
def test_old_reading_with_synced_clock_is_replayed_with_capture_time(tmp_path):
    up, post, clock = make(tmp_path, synced=True)
    up.submit("st1", {"n": 1})
    captured = clock.t
    clock.t += 3 * 3600                           # 3 hours later
    assert up.attempt_next() == "sent"
    body = post.calls[0]
    assert body["replayed"] is True
    from datetime import datetime, timezone
    assert datetime.fromisoformat(body["client_timestamp"]) == datetime.fromtimestamp(captured, tz=timezone.utc)


def test_old_reading_with_unsynced_clock_is_dropped_not_misstamped(tmp_path):
    up, post, clock = make(tmp_path, synced=False)
    up.submit("st1", {"n": 1})
    clock.t += 3600
    assert up.attempt_next() == "dropped" and post.calls == [] and len(up.queue) == 0


def test_recent_reading_with_unsynced_clock_is_still_sent_live(tmp_path):
    up, post, clock = make(tmp_path, synced=False)
    up.submit("st1", {"n": 1})
    clock.t += 30
    assert up.attempt_next() == "sent"
    assert "replayed" not in post.calls[0]


def test_boundary_of_live_window(tmp_path):
    up, post, clock = make(tmp_path)
    up.submit("st1", {"n": 1})
    clock.t += cu.LIVE_MAX_AGE_SEC                # exactly at the limit is still live
    up.attempt_next()
    assert "replayed" not in post.calls[0]


def test_clock_stepped_backwards_is_treated_as_live(tmp_path):
    up, post, clock = make(tmp_path)
    up.submit("st1", {"n": 1})
    clock.t -= 500                                # NTP corrected the clock backwards
    up.attempt_next()
    assert "replayed" not in post.calls[0]


# ---------------------------------------------------------------------------
# Durability
# ---------------------------------------------------------------------------
def test_queue_survives_restart(tmp_path):
    up, _, clock = make(tmp_path, post=FakePost(default=503))
    up.submit("st1", {"n": 1})
    up.submit("st1", {"n": 2})
    up.queue.close()                              # simulate power loss / reboot

    up2, post2, _ = make(tmp_path, clock=clock)
    assert len(up2.queue) == 2
    assert up2.attempt_next() == "sent" and up2.attempt_next() == "sent"
    assert [c["n"] for c in post2.calls] == [1, 2]


def test_corrupt_queue_file_is_quarantined_and_replaced(tmp_path):
    path = tmp_path / "q.sqlite3"
    path.write_bytes(b"this is not a sqlite database" * 50)
    up, post, _ = make(tmp_path)
    assert len(up.queue) == 0
    assert list(tmp_path.glob("q.sqlite3.corrupt-*")), "bad file should be kept for inspection"
    up.submit("st1", {"n": 1})
    assert up.attempt_next() == "sent"


def test_queue_cap_drops_oldest(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="awh.uploader")
    up, post, _ = make(tmp_path, post=FakePost(default=503), max_queue_rows=3)
    for i in range(5):
        up.submit("st1", {"n": i})
    assert len(up.queue) == 3
    assert json.loads(up.queue.oldest()["payload"]) == {"n": 2}
    assert "queue full" in caplog.text


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------
def test_thread_delivers_and_stops_cleanly(tmp_path):
    up, post, _ = make(tmp_path)
    up.start()
    for i in range(3):
        up.submit("st1", {"n": i})
    deadline = time.time() + 5
    while len(post.calls) < 3 and time.time() < deadline:
        time.sleep(0.02)
    up.stop()
    assert [c["n"] for c in post.calls] == [0, 1, 2]
    assert not up._thread.is_alive()


def test_thread_retries_with_backoff_then_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(cu, "BACKOFF_START_SEC", 0.01)
    post = FakePost([requests_error(), requests_error()])
    up, _, _ = make(tmp_path, post=post)
    up.start()
    up.submit("st1", {"n": 1})
    deadline = time.time() + 5
    while up.stats()["queued"] and time.time() < deadline:
        time.sleep(0.02)
    up.stop()
    assert up.stats()["queued"] == 0 and len(post.calls) == 3
    assert len({c["reading_id"] for c in post.calls}) == 1


def test_slow_post_does_not_block_submit(tmp_path):
    """The whole point: a hanging network call must not stall the caller."""
    gate = threading.Event()

    def hanging_post(url, json=None, headers=None, timeout=None):
        gate.wait(5)
        return Resp(200)

    up, _, _ = make(tmp_path, post=hanging_post)
    up.start()
    up.submit("st1", {"n": 0})
    time.sleep(0.1)                               # uploader is now stuck inside post()
    t0 = time.time()
    for i in range(1, 50):
        up.submit("st1", {"n": i})
    assert time.time() - t0 < 1.0
    gate.set()
    up.stop()


# ---------------------------------------------------------------------------
# Clock-sync helper and logging
# ---------------------------------------------------------------------------
class Proc:
    def __init__(self, out):
        self.stdout = out


@pytest.fixture(autouse=True)
def _reset_clock_cache(monkeypatch):
    cu._clock_cache.update(at=0.0, value=False)
    monkeypatch.delenv("AWH_STATION_KEY", raising=False)  # a dev machine's real key must not leak into tests


def test_is_clock_synced_yes_no_and_failure():
    assert cu.is_clock_synced(run=lambda *a, **k: Proc("yes\n"), now=lambda: 1000.0) is True
    cu._clock_cache.update(at=0.0)
    assert cu.is_clock_synced(run=lambda *a, **k: Proc("no\n"), now=lambda: 1000.0) is False
    cu._clock_cache.update(at=0.0)

    def boom(*a, **k):
        raise FileNotFoundError("timedatectl")
    assert cu.is_clock_synced(run=boom, now=lambda: 1000.0) is False  # e.g. a Mac: be conservative


def test_is_clock_synced_is_cached():
    calls = []

    def run(*a, **k):
        calls.append(1)
        return Proc("yes")
    cu.is_clock_synced(run=run, now=lambda: 5000.0)
    cu.is_clock_synced(run=run, now=lambda: 5010.0)
    assert len(calls) == 1


def test_file_logging_is_idempotent_and_writes(tmp_path):
    logger = cu.setup_file_logging(str(tmp_path / "logs"))
    cu.setup_file_logging(str(tmp_path / "logs"))
    assert len([h for h in logger.handlers if getattr(h, "_awh_station", False)]) == 2  # file + console, once
    logging.getLogger("awh.uploader").warning("hello-from-test")
    for h in logger.handlers:
        h.flush()
    assert "hello-from-test" in (tmp_path / "logs" / "station.log").read_text()


# ---------------------------------------------------------------------------
# Station key
# ---------------------------------------------------------------------------
KEY = "k-ASU-1234567890abcdef"


def test_key_from_file_is_sent_as_header(tmp_path):
    (tmp_path / "station_key").write_text(f"  {KEY}\n# comment-less file, extra lines ignored\n")
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(headers=headers)
        return Resp(200)

    up, _, _ = make(tmp_path, post=post)
    up.submit("st1", {"n": 1})
    assert up.attempt_next() == "sent"
    assert seen["headers"] == {"X-Station-Key": KEY}


def test_env_key_beats_file(tmp_path, monkeypatch):
    (tmp_path / "station_key").write_text("file-key-aaaaaaaa")
    monkeypatch.setenv("AWH_STATION_KEY", KEY)
    up, _, _ = make(tmp_path)
    assert up._headers == {"X-Station-Key": KEY}


def test_no_key_sends_no_header_and_warns_once(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="awh.uploader")
    up, post, _ = make(tmp_path)
    assert up._headers == {}
    assert caplog.text.count("no station key configured") == 1


@pytest.mark.parametrize("content", ["", "   \n", "\n\n"])
def test_blank_key_file_means_no_key(tmp_path, content):
    (tmp_path / "station_key").write_text(content)
    up, _, _ = make(tmp_path)
    assert up._headers == {}


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_key_keeps_readings_queued_not_dropped(tmp_path, status):
    """A wrong/rotated key must never cause data loss: readings wait for an operator fix."""
    up, _, clock = make(tmp_path, post=FakePost(default=status))
    for i in range(3):
        up.submit("st1", {"n": i})
        assert up.attempt_next() == "retry"
    assert len(up.queue) == 3
    assert "station key rejected" in up.stats()["last_error"]


def test_readings_flow_again_after_key_is_fixed(tmp_path):
    post = FakePost([401, 401])
    up, _, _ = make(tmp_path, post=post)
    up.submit("st1", {"n": 1})
    up.submit("st1", {"n": 2})
    assert up.attempt_next() == "retry" and up.attempt_next() == "retry"
    # operator fixes the key; cloud now accepts
    assert up.attempt_next() == "sent" and up.attempt_next() == "sent"
    assert [c["n"] for c in post.calls[-2:]] == [1, 2] and len(up.queue) == 0


def test_key_never_appears_in_logs(tmp_path, caplog):
    (tmp_path / "station_key").write_text(KEY)
    caplog.set_level(logging.DEBUG)
    up, _, _ = make(tmp_path, post=FakePost([401, 500, requests_error()], default=200))
    for i in range(4):
        up.submit("st1", {"n": i})
        up.attempt_next()
    assert KEY not in caplog.text
    assert KEY not in json.dumps(up.stats(), default=str)
    assert KEY not in json.dumps(json.loads(up.queue.oldest()["payload"]) if up.queue.oldest() else {})


def test_successful_upload_logs_an_info_line_for_the_operator(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="awh.uploader")
    up, _, _ = make(tmp_path)
    up.submit("st1", {"n": 1})
    assert up.attempt_next() == "sent"
    assert "[Cloud Upload] 200 OK" in caplog.text and "0 still queued" in caplog.text
