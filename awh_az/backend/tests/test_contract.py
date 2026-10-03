"""Contract tests: the dashboard's hand-written TypeScript types vs what the backend really returns.

Nothing else links awh_az/water-station-dashboard/src/lib/api-client.ts to the Pydantic models, so a
renamed or removed backend field would silently turn into `undefined` in the UI. These tests fail
instead. Names must match in both directions; value kinds (string/number/...) must agree.

Skipped when the dashboard sources aren't present next to the backend (e.g. a backend-only checkout).
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import main

API_CLIENT = Path(__file__).resolve().parents[2] / "water-station-dashboard" / "src" / "lib" / "api-client.ts"
pytestmark = pytest.mark.skipif(not API_CLIENT.exists(), reason="dashboard sources not present")


# ---------------------------------------------------------------------------
# Tiny TypeScript interface reader (these interfaces are flat `name?: type;` lists)
# ---------------------------------------------------------------------------
def ts_interface(name: str) -> dict:
    src = re.sub(r"/\*.*?\*/", "", API_CLIENT.read_text(), flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    m = re.search(r"export interface %s\s*\{(.*?)\n\}" % re.escape(name), src, re.S)
    assert m, f"interface {name} not found in api-client.ts"
    fields = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"\s*(\w+)(\?)?\s*:\s*(.+?);?\s*$", line)
        if mm:
            fields[mm.group(1)] = {"optional": bool(mm.group(2)), "type": mm.group(3).rstrip(";").strip()}
    assert fields, f"interface {name} has no parsable fields"
    return fields


def ts_kind(type_text: str) -> str:
    """Coarse kind of a TS type: string | number | boolean | array | object | any."""
    parts = [p.strip() for p in type_text.split("|") if p.strip() not in ("null", "undefined")]
    if len(parts) != 1:
        return "any"  # unions of real types (string literals etc.) aren't compared
    t = parts[0]
    if t.endswith("[]") or t.startswith("Array<"):
        return "array"
    if t in ("string", "number", "boolean"):
        return t
    if t.startswith("Record<") or t.startswith("{"):
        return "object"
    if re.fullmatch(r"'[^']*'(\s*\|\s*'[^']*')*", t):
        return "string"
    return "any"  # another interface name, etc.


def py_kind(schema: dict) -> str:
    options = schema.get("anyOf") or [schema]
    kinds = set()
    for o in options:
        if o.get("type") == "null":
            continue
        t = o.get("type")
        if "$ref" in o:
            kinds.add("any")
        elif t in ("integer", "number"):
            kinds.add("number")
        elif t in ("string", "boolean", "array", "object"):
            kinds.add(t)
        else:
            kinds.add("any")
    return kinds.pop() if len(kinds) == 1 else "any"


# ---------------------------------------------------------------------------
# Models that have an OpenAPI schema
# ---------------------------------------------------------------------------
MODELS = ["StationReading", "StationMetadata", "StationInfo", "ReadingsResponse",
          "BulkExportRequest", "HealthResponse", "StationImpact", "ImpactResponse"]


@pytest.mark.parametrize("name", MODELS)
def test_ts_interface_matches_backend_model(name):
    schema = main.app.openapi()["components"]["schemas"][name]
    py_props = schema["properties"]
    ts = ts_interface(name)

    assert set(py_props) - set(ts) == set(), f"{name}: backend fields missing from the TypeScript type"
    assert set(ts) - set(py_props) == set(), f"{name}: TypeScript fields the backend does not provide"

    mismatches = []
    for field, prop in py_props.items():
        pk, tk = py_kind(prop), ts_kind(ts[field]["type"])
        if "any" not in (pk, tk) and pk != tk:
            mismatches.append(f"{field}: backend {pk}, TypeScript {tk}")
    assert not mismatches, f"{name}: value kinds differ: {mismatches}"


def test_every_documented_response_model_is_checked():
    """If someone adds a response model the dashboard consumes, make them add it to MODELS."""
    schemas = set(main.app.openapi()["components"]["schemas"])
    dashboard_facing = {"StationReading", "StationMetadata", "StationInfo", "ReadingsResponse",
                        "BulkExportRequest", "HealthResponse", "StationImpact", "ImpactResponse"}
    assert dashboard_facing <= schemas


# ---------------------------------------------------------------------------
# /hourly has no response_model, so test the real output instead
# ---------------------------------------------------------------------------
def hourly_readings(hours=3):
    """One reading a minute for `hours` hours, ascending, with every sensor the maths uses."""
    t0 = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)
    out = []
    for i in range(hours * 60):
        out.append({
            "station_name": "x", "timestamp": (t0 + timedelta(minutes=i)).isoformat(), "unit": "m/s",
            "temperature": 30.0 + (i % 5), "humidity": 40.0 + (i % 7), "velocity": 2.0,
            "outtake_temperature": 25.0, "outtake_humidity": 70.0, "outtake_velocity": 1.5, "outtake_unit": "m/s",
            "weight": 100.0 + i * 2.0, "power": 1200.0 + i, "voltage": 230.0, "current": 5.2,
            "energy": 1000.0 + i * 20.0, "pump_status": 1,
        })
    return out


def test_hourly_response_matches_typescript_types(client, use_firestore):
    use_firestore({"x": {"readings": hourly_readings(), "meta": {}}})
    r = client.get("/stations/x/hourly")
    assert r.status_code == 200
    body = r.json()

    top = ts_interface("HourlyAggregationResponse")
    assert set(body) == set(top), f"top-level keys differ: backend {sorted(body)} vs TypeScript {sorted(top)}"

    row_ts = ts_interface("HourlyDataRow")
    assert len(body["data"]) >= 3
    for row in body["data"]:
        extra = set(row) - set(row_ts)
        missing_required = {k for k, v in row_ts.items() if not v["optional"]} - set(row)
        assert not extra, f"backend returns hourly fields the TypeScript type doesn't know: {sorted(extra)}"
        assert not missing_required, f"TypeScript requires hourly fields the backend didn't return: {sorted(missing_required)}"


def test_hourly_value_kinds_match_typescript(client, use_firestore):
    use_firestore({"x": {"readings": hourly_readings(), "meta": {}}})
    body = client.get("/stations/x/hourly").json()
    row_ts = ts_interface("HourlyDataRow")
    bad = []
    for row in body["data"]:
        for k, v in row.items():
            tk = ts_kind(row_ts[k]["type"])
            if v is None:
                if "null" not in row_ts[k]["type"] and not row_ts[k]["optional"]:
                    bad.append(f"{k}: null but TypeScript type is {row_ts[k]['type']}")
            elif isinstance(v, str) and tk != "string":
                bad.append(f"{k}: string but TypeScript {tk}")
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and tk != "number":
                bad.append(f"{k}: number but TypeScript {tk}")
    assert not bad, bad[:10]
