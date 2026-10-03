"""Tests for the streaming POST /export."""
import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest

import main

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


class PDoc:
    def __init__(self, i, station):
        self.i, self.station = i, station

    def to_dict(self):
        return {"station_name": self.station, "timestamp": T0 + timedelta(minutes=self.i),
                "temperature": 20.0 + self.i, "weight": float(self.i), "unit": "AquaPars",
                "secret_extra_field": "should-not-be-exported"}


class PagedQuery:
    """Firestore-like query with real cursor pagination over `n` readings."""

    def __init__(self, station, n, tracker, lo=0, k=10**9):
        self.station, self.n, self.tracker, self.lo, self.k = station, n, tracker, lo, k

    def order_by(self, *a, **kw):
        return self

    def where(self, *a, **kw):
        return self

    def limit(self, k):
        return PagedQuery(self.station, self.n, self.tracker, self.lo, k)

    def start_after(self, doc):
        return PagedQuery(self.station, self.n, self.tracker, doc.i + 1, self.k)

    def stream(self):
        self.tracker["pages"] += 1
        return iter([PDoc(i, self.station) for i in range(self.lo, min(self.lo + self.k, self.n))])


class PagedDb:
    def __init__(self, counts, tracker=None):
        self.counts, self.tracker = counts, tracker if tracker is not None else {"pages": 0}
        self._station = None

    def collection(self, name):
        return self

    def document(self, name):
        self._station = name
        return self

    def stream(self):  # list of stations when none are named
        return iter([type("S", (), {"id": n})() for n in self.counts])

    def __getattr__(self, name):  # .collection("readings") chain ends here
        raise AttributeError(name)


class Wrapper(PagedDb):
    def collection(self, name):
        if name == "readings":
            return PagedQuery(self._station, self.counts[self._station], self.tracker)
        return self


@pytest.fixture
def paged(monkeypatch):
    def install(counts, page=3):
        db = Wrapper(counts)
        monkeypatch.setattr(main, "db", db)
        monkeypatch.setattr(main.settings, "max_query_limit", page)
        return db
    return install


def parse_csv(text):
    return list(csv.DictReader(io.StringIO(text)))


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------
def test_csv_has_every_row_across_pages(client, paged):
    paged({"a@Lab": 8}, page=3)  # 3 pages: 3 + 3 + 2
    r = client.post("/export", json={"station_names": ["a@Lab"], "format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=station_data_" in r.headers["content-disposition"]
    rows = parse_csv(r.text)
    assert [float(x["weight"]) for x in rows] == [float(i) for i in range(8)]
    assert rows[0]["station_name"] == "a@Lab" and rows[0]["timestamp"].startswith("2026-01-01T00:00")


def test_exact_page_boundary_does_not_drop_or_duplicate(client, paged):
    paged({"a@Lab": 6}, page=3)  # exactly two full pages, then an empty third
    rows = parse_csv(client.post("/export", json={"station_names": ["a@Lab"]}).text)
    assert len(rows) == 6 and len({r["weight"] for r in rows}) == 6


def test_header_is_sorted_fixed_columns_and_extras_are_dropped(client, paged):
    paged({"a@Lab": 2})
    text = client.post("/export", json={"station_names": ["a@Lab"]}).text
    header = text.splitlines()[0].split(",")
    assert header == sorted(header) and {"station_name", "timestamp", "temperature", "weight"} <= set(header)
    assert "secret_extra_field" not in text            # unknown document keys never leak into the export


def test_fields_filter_limits_columns(client, paged):
    paged({"a@Lab": 2})
    rows = parse_csv(client.post("/export", json={"station_names": ["a@Lab"], "fields": ["temperature"]}).text)
    assert set(rows[0]) == {"station_name", "timestamp", "temperature"}


def test_multiple_stations_are_concatenated_in_order(client, paged):
    paged({"a@Lab": 2, "b@Lab": 3})
    rows = parse_csv(client.post("/export", json={"station_names": ["b@Lab", "a@Lab"]}).text)
    assert [r["station_name"] for r in rows] == ["b@Lab"] * 3 + ["a@Lab"] * 2


def test_no_station_names_exports_all_stations(client, paged):
    paged({"a@Lab": 1, "b@Lab": 2})
    rows = parse_csv(client.post("/export", json={"format": "csv"}).text)
    assert {r["station_name"] for r in rows} == {"a@Lab", "b@Lab"} and len(rows) == 3


def test_json_is_a_valid_array(client, paged):
    paged({"a@Lab": 5}, page=2)
    r = client.post("/export", json={"station_names": ["a@Lab"], "format": "json"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    data = json.loads(r.text)
    assert [d["weight"] for d in data] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert "secret_extra_field" in data[0]              # JSON still carries every stored key, as before


def test_excluded_window_rows_are_dropped(client, paged, monkeypatch):
    paged({"a@Lab": 5})
    monkeypatch.setitem(main.EXCLUDED_DATA_RANGES, "a@Lab", [("2026-01-01T00:01:00", "2026-01-01T00:03:00")])
    rows = parse_csv(client.post("/export", json={"station_names": ["a@Lab"]}).text)
    assert [float(r["weight"]) for r in rows] == [0.0, 3.0, 4.0]   # minutes 1 and 2 removed


# ---------------------------------------------------------------------------
# Errors that must still be real HTTP errors (decided before streaming starts)
# ---------------------------------------------------------------------------
def test_no_data_is_404(client, paged):
    paged({"a@Lab": 0})
    assert client.post("/export", json={"station_names": ["a@Lab"]}).status_code == 404


def test_parquet_fails_fast_without_touching_firestore(client, monkeypatch):
    class Boom:
        def __getattr__(self, name):
            raise AssertionError("Firestore must not be queried for an unsupported format")
    monkeypatch.setattr(main, "db", Boom())
    r = client.post("/export", json={"station_names": ["a@Lab"], "format": "parquet"})
    assert r.status_code == 400 and "not yet implemented" in r.json()["detail"]


def test_invalid_format_is_422(client, paged):
    paged({"a@Lab": 1})
    assert client.post("/export", json={"format": "xml"}).status_code == 422


def test_503_without_firestore(client):
    assert client.post("/export", json={"format": "csv"}).status_code == 503


# ---------------------------------------------------------------------------
# Streaming properties (the point of the change)
# ---------------------------------------------------------------------------
def test_rows_are_read_lazily_not_all_up_front(paged, monkeypatch):
    db = paged({"a@Lab": 30}, page=3)                       # 10 pages
    monkeypatch.setattr(main, "EXPORT_CHUNK_BYTES", 200)    # tiny chunks so the first one is small
    rows = main._iter_export_rows(["a@Lab"], None, None, None)
    chunks = main._csv_chunks(rows, main._export_columns(None))
    next(chunks)
    assert db.tracker["pages"] < 10, "the first chunk must not require reading the whole range"


def test_large_export_is_sent_in_many_chunks(paged, monkeypatch):
    paged({"a@Lab": 500}, page=100)
    monkeypatch.setattr(main, "EXPORT_CHUNK_BYTES", 2000)
    chunks = list(main._csv_chunks(main._iter_export_rows(["a@Lab"], None, None, None), main._export_columns(None)))
    assert len(chunks) > 10 and all(isinstance(c, bytes) for c in chunks)
    assert len(parse_csv(b"".join(chunks).decode())) == 500


def test_failure_mid_stream_is_not_swallowed(paged, monkeypatch):
    """A read error after the download started must abort it, never yield a quietly truncated file."""
    def rows():
        yield {"station_name": "a", "timestamp": "t", "weight": 1}
        raise RuntimeError("firestore went away")
    monkeypatch.setattr(main, "EXPORT_CHUNK_BYTES", 1)
    with pytest.raises(RuntimeError, match="firestore went away"):
        list(main._csv_chunks(rows(), main._export_columns(None)))
    with pytest.raises(RuntimeError, match="firestore went away"):
        list(main._json_chunks(rows()))


def test_json_chunks_join_to_valid_json_for_one_and_many_rows():
    one = b"".join(main._json_chunks(iter([{"a": 1}])))
    assert json.loads(one) == [{"a": 1}]
    many = b"".join(main._json_chunks(iter({"i": i} for i in range(1000))))
    assert [d["i"] for d in json.loads(many)] == list(range(1000))
