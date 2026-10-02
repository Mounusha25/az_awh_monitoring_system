"""Shared fixtures for backend API tests.

Nothing here may touch real Firestore or Postgres: the lifespan startup hooks
are patched to no-ops (a real service-account key exists in this directory on
dev machines), and `main.db` / `main.db_pool` are swapped for fakes per test.
"""
import os
import sys
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from cache import cache  # noqa: E402


class FakeDoc:
    def __init__(self, doc_id="", data=None):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeQuery:
    """Stands in for a Firestore query/collection over a fixed, pre-ordered list."""

    def __init__(self, docs):
        self._docs = list(docs)

    def order_by(self, *a, **k):
        return self

    def where(self, *a, **k):
        return self

    def start_after(self, *a, **k):
        return self

    def limit(self, n):
        return FakeQuery(self._docs[:n])

    def stream(self):
        return iter(self._docs)


class FakeStationRef:
    def __init__(self, name, readings, meta, aggregates):
        self.id = name
        self._readings = readings
        self._meta = meta
        self._aggregates = aggregates

    def get(self):
        return FakeDoc(self.id, self._meta)

    def collection(self, name):
        if name == "readings":
            return FakeQuery([FakeDoc(f"r{i}", r) for i, r in enumerate(self._readings)])
        if name == "aggregates":
            return FakeAggregates(self._aggregates)
        raise KeyError(name)


class FakeAggregates:
    def __init__(self, totals):
        self._totals = totals

    def document(self, name):
        return FakeDocRef(FakeDoc(name, self._totals if name == "lifetime_totals" else None))


class FakeDocRef:
    def __init__(self, doc):
        self._doc = doc

    def get(self):
        return self._doc


class FakePool:
    """Truthy stand-in for the psycopg2 pool; lifespan shutdown calls closeall()."""

    def closeall(self):
        pass


class FakeFirestore:
    """stations: {name: {"readings": [newest-first dicts], "meta": {}, "totals": {} | None}}"""

    def __init__(self, stations=None):
        self._stations = stations or {}

    def collection(self, _name):
        return self

    def list_documents(self):
        return [self.document(n) for n in self._stations]

    def document(self, name):
        s = self._stations.get(name, {})
        return FakeStationRef(name, s.get("readings", []), s.get("meta", {}), s.get("totals"))


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    """Per-test: no real startup hooks, empty cache, no db/pool, no admin key."""
    for hook in ("init_firestore", "init_storage", "ensure_default_registry_stations", "init_postgres"):
        monkeypatch.setattr(main, hook, lambda: None)
    monkeypatch.setattr(main, "db", None)
    monkeypatch.setattr(main, "db_pool", None)
    monkeypatch.setattr(main.settings, "admin_api_key", "")
    cache.flush_all()
    yield
    cache.flush_all()


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def use_firestore(monkeypatch):
    """Install a FakeFirestore as main.db and return it."""
    def _install(stations=None):
        fake = FakeFirestore(stations)
        monkeypatch.setattr(main, "db", fake)
        return fake
    return _install


def reading(ts=None, **fields):
    """A Firestore-style reading dict; defaults to 'now' so stations show active."""
    ts = ts or datetime.now(timezone.utc).isoformat()
    return {"station_name": "test", "timestamp": ts, "unit": "AquaPars", **fields}
