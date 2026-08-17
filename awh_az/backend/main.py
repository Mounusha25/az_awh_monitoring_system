from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.concurrency import run_in_threadpool
from typing import List, Optional
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import asyncio
import json
import io
import csv
import os
import logging

import base64
import tempfile

import firebase_admin
from firebase_admin import credentials, firestore

from models import (
    StationReading,
    StationInfo,
    StationMetadata,
    ReadingsResponse,
    BulkExportRequest,
    HealthResponse,
    StationRegistryItem,
    StationRegistryResponse,
    CreateStationRequest,
)
from config import settings
from cache import cache, get_stations_cache_key, get_station_readings_cache_key, invalidate_station_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Firebase initialization
# ---------------------------------------------------------------------------
db = None

DEFAULT_REGISTRY_STATIONS = [
    {
        "station_name": "station_testbed_1@Powerplant",
        "location": "Powerplant",
    },
]


def init_firestore():
    global db
    # Option 1: Base64-encoded JSON in env var (for Render / cloud deploys)
    cred_json_b64 = os.environ.get("FIREBASE_CREDENTIALS_JSON")
    if cred_json_b64:
        try:
            cred_dict = json.loads(base64.b64decode(cred_json_b64))
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            db = firestore.client()
            logger.info("✅ Firestore connected (from env var)")
            return
        except Exception as e:
            logger.error(f"Failed to load credentials from FIREBASE_CREDENTIALS_JSON: {e}")

    # Option 2: File path (local development)
    cred_path = settings.firebase_credentials_path
    if not os.path.exists(cred_path):
        logger.error(f"Firebase credentials not found at {cred_path}")
        return
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    logger.info("✅ Firestore connected (from file)")


def ensure_default_registry_stations():
    """Seed required registry stations so new deployments appear in the UI automatically."""
    if not db:
        return

    stations_ref = db.collection(settings.firestore_collection)
    for station in DEFAULT_REGISTRY_STATIONS:
        station_name = station["station_name"].strip()
        doc_ref = stations_ref.document(station_name)
        if doc_ref.get().exists:
            continue

        doc_ref.set({
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "PENDING",
            "location": station.get("location", ""),
        })
        logger.info(f"✅ Seeded registry station: {station_name}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
SKIP_FIELDS = {"station_name", "timestamp"}

FIELD_CATEGORIES = {
    "temperature": "Intake Air",
    "humidity": "Intake Air",
    "velocity": "Intake Air",
    "outtake_humidity": "Outtake Air",
    "outtake_velocity": "Outtake Air",
    "outtake_temperature": "Outtake Air",
    "outtake_unit": "Outtake Air",
    "flow_lmin": "Water Production",
    "flow_hz": "Water Production",
    "flow_total": "Water Production",
    "weight": "Water Production",
    "power": "Power Consumption",
    "voltage": "Power Consumption",
    "current": "Power Consumption",
    "energy": "Power Consumption",
    "pump_status": "System",
    "unit": "System",
}


def _firestore_doc_to_dict(doc_dict: dict) -> dict:
    """Convert Firestore document dict to JSON-safe dict."""
    out = {}
    for k, v in doc_dict.items():
        # Firestore DatetimeWithNanoseconds -> ISO string
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _build_field_groups(available_fields: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for f in available_fields:
        cat = FIELD_CATEGORIES.get(f)
        if cat:
            groups.setdefault(cat, []).append(f)
    return groups


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_firestore()
    ensure_default_registry_stations()
    if db:
        print("✅ Firestore initialised – ready to serve")
    else:
        print("⚠️  Firestore NOT initialised – check serviceAccountKey.json")
    yield


app = FastAPI(
    title="AWH Station Monitoring API",
    description="API for monitoring Atmospheric Water Harvesting stations — backed by Firestore",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.all_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/", tags=["Health"])
async def root():
    return {"message": "AWH Station Monitoring API", "version": "2.0.0", "docs": "/docs"}


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    redis_status = "online" if cache.health_check() else "offline"
    fs_status = "online" if db else "offline"
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(timezone.utc),
        services={"api": "online", "redis": redis_status, "firestore": fs_status},
    )


# ---------------------------------------------------------------------------
# Stations list
# ---------------------------------------------------------------------------
@app.get("/stations", response_model=List[StationInfo], tags=["Stations"])
async def get_stations():
    """List all stations with metadata derived from their latest readings."""
    if not db:
        raise HTTPException(status_code=503, detail="Firestore not initialised")

    cache_key = get_stations_cache_key()
    cached = cache.get(cache_key)
    if cached:
        return cached

    stations_ref = db.collection(settings.firestore_collection)
    station_docs = list(stations_ref.list_documents())

    def _fetch_station(sdoc) -> Optional[StationInfo]:
        sname = sdoc.id
        readings_ref = (
            stations_ref.document(sname)
            .collection("readings")
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(50)
        )
        reading_docs = list(readings_ref.stream())
        if not reading_docs:
            return None

        available_fields: set[str] = set()
        for rdoc in reading_docs:
            for key, val in rdoc.to_dict().items():
                if val is not None and key not in SKIP_FIELDS:
                    available_fields.add(key)

        available_list = sorted(available_fields)
        latest = _firestore_doc_to_dict(reading_docs[0].to_dict())

        metadata = StationMetadata(
            station_name=sname,
            available_fields=available_list,
            field_groups=_build_field_groups(available_list),
            last_reading=latest.get("timestamp"),
            total_readings=len(reading_docs),
        )

        station_status = "inactive"
        last_ts_raw = latest.get("timestamp")
        if last_ts_raw:
            try:
                last_dt = datetime.fromisoformat(last_ts_raw) if isinstance(last_ts_raw, str) else last_ts_raw
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600 <= 48:
                    station_status = "active"
            except Exception:
                pass

        return StationInfo(
            station_name=sname,
            unit=latest.get("unit", "Unknown"),
            location=latest.get("location"),
            status=station_status,
            metadata=metadata,
        )

    # Fetch all stations in parallel — eliminates N sequential Firestore round-trips
    with ThreadPoolExecutor(max_workers=min(len(station_docs), 16)) as executor:
        results = list(executor.map(_fetch_station, station_docs))

    stations: list[StationInfo] = [s for s in results if s is not None]

    cache.set(cache_key, [s.dict() for s in stations], ttl=300)
    return stations


def _fetch_readings_docs(readings_ref, offset: int, limit: int):
    """Blocking Firestore fetch (cursor-skip + paged read). Runs in a worker
    thread via run_in_threadpool — up to `limit` (max 10000) docs can be
    streamed here, and calling .stream() directly from the async route would
    block the event loop for the whole fetch. Returns None if offset exceeds
    the total number of documents.
    """
    if offset > 0:
        cursor_docs = list(readings_ref.limit(offset).stream())
        if len(cursor_docs) == offset:
            readings_ref = readings_ref.start_after(cursor_docs[-1])
        else:
            return None
    return list(readings_ref.limit(limit).stream())


# ---------------------------------------------------------------------------
# Station readings
# ---------------------------------------------------------------------------
@app.get("/stations/{station_name}/readings", response_model=ReadingsResponse, tags=["Readings"])
async def get_station_readings(
    station_name: str,
    start_date: Optional[datetime] = Query(None, description="Start date filter (ISO format)"),
    end_date: Optional[datetime] = Query(None, description="End date filter (ISO format)"),
    fields: Optional[str] = Query(None, description="Comma-separated list of fields to include"),
    limit: int = Query(100, le=10000, description="Maximum number of records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
):
    """Get readings for a specific station with filtering and pagination."""
    if not db:
        raise HTTPException(status_code=503, detail="Firestore not initialised")

    cache_key = get_station_readings_cache_key(
        station_name, limit, offset,
        start_date.isoformat() if start_date else None,
        end_date.isoformat() if end_date else None,
        fields,
    )
    cached = cache.get(cache_key)
    if cached:
        return cached

    # When a start_date is given, order ASCENDING so limit returns records
    # from the start of the range forward (not the most recent N records).
    # When no start_date, order DESCENDING to get the latest readings first.
    order_dir = firestore.Query.ASCENDING if start_date else firestore.Query.DESCENDING

    readings_ref = (
        db.collection(settings.firestore_collection)
        .document(station_name)
        .collection("readings")
        .order_by("timestamp", direction=order_dir)
    )

    if start_date:
        readings_ref = readings_ref.where("timestamp", ">=", start_date)
    if end_date:
        readings_ref = readings_ref.where("timestamp", "<=", end_date)

    # Cursor-based pagination: skip `offset` docs without downloading them all.
    # For offset=0 (the common case) we go straight to .limit(limit).
    docs = await run_in_threadpool(_fetch_readings_docs, readings_ref, offset, limit)

    if not docs:
        raise HTTPException(status_code=404, detail=f"Station '{station_name}' not found or has no readings")

    # Parse readings
    requested_fields = None
    if fields:
        requested_fields = set(f.strip() for f in fields.split(","))
        requested_fields.update({"station_name", "timestamp", "unit"})

    readings_list: list[dict] = []
    available_fields: set[str] = set()

    for rdoc in docs:
        data = _firestore_doc_to_dict(rdoc.to_dict())

        for key, val in data.items():
            if val is not None and key not in SKIP_FIELDS:
                available_fields.add(key)

        if requested_fields:
            data = {k: v for k, v in data.items() if k in requested_fields}

        readings_list.append(data)

    total = offset + len(docs)  # best estimate without a full count scan

    metadata = StationMetadata(
        station_name=station_name,
        available_fields=sorted(available_fields),
        field_groups=_build_field_groups(sorted(available_fields)),
        total_readings=total,
    )

    readings = [StationReading(**r) for r in readings_list]

    response = ReadingsResponse(
        data=readings,
        total=total,
        limit=limit,
        offset=offset,
        metadata=metadata,
    )

    cache.set(cache_key, response.dict(), ttl=180)
    return response


def _fetch_station_export_rows(
    sname: str,
    start_date: Optional[datetime],
    end_date: Optional[datetime],
    fields: Optional[set],
) -> list[dict]:
    """Blocking Firestore fetch for one station's export rows.

    Runs inside run_in_threadpool — the firestore-admin client is synchronous,
    so calling .stream() directly from an async route would block the single
    event loop for the entire paginated fetch, stalling every other request
    the server is handling in the meantime.
    """
    export_order = firestore.Query.ASCENDING if start_date else firestore.Query.DESCENDING
    query = (
        db.collection(settings.firestore_collection)
        .document(sname)
        .collection("readings")
        .order_by("timestamp", direction=export_order)
    )
    if start_date:
        query = query.where("timestamp", ">=", start_date)
    if end_date:
        query = query.where("timestamp", "<=", end_date)

    rows: list[dict] = []
    # Paginate through all records — a single .limit(max_query_limit) caps at ~7 days
    # at 1 reading/minute. Loop in batches until Firestore returns a partial page.
    page_query = query.limit(settings.max_query_limit)
    while True:
        batch = list(page_query.stream())
        if not batch:
            break
        for rdoc in batch:
            data = _firestore_doc_to_dict(rdoc.to_dict())
            if fields:
                data = {k: v for k, v in data.items() if k in fields or k in ("station_name", "timestamp")}
            rows.append(data)
        if len(batch) < settings.max_query_limit:
            break
        page_query = query.start_after(batch[-1]).limit(settings.max_query_limit)
    return rows


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@app.post("/export", tags=["Export"])
async def export_data(request: BulkExportRequest):
    """Export station data in CSV or JSON format."""
    if not db:
        raise HTTPException(status_code=503, detail="Firestore not initialised")

    # Determine which stations to query
    if request.station_names:
        station_names = request.station_names
    else:
        station_names = await run_in_threadpool(
            lambda: [doc.id for doc in db.collection(settings.firestore_collection).stream()]
        )

    fields = set(request.fields) if request.fields else None

    # Fetch every station concurrently in worker threads instead of one
    # sequential blocking pagination loop per station on the event loop.
    per_station_rows = await asyncio.gather(*[
        run_in_threadpool(_fetch_station_export_rows, sname, request.start_date, request.end_date, fields)
        for sname in station_names
    ])
    all_readings: list[dict] = [row for rows in per_station_rows for row in rows]

    if not all_readings:
        raise HTTPException(status_code=404, detail="No data found matching the criteria")

    if request.format == "csv":
        output = io.StringIO()
        all_keys: set[str] = set()
        for r in all_readings:
            all_keys.update(r.keys())
        fieldnames = sorted(all_keys)
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_readings)
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=station_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"},
        )

    elif request.format == "json":
        json_data = json.dumps(all_readings, indent=2, default=str)
        return StreamingResponse(
            io.BytesIO(json_data.encode()),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=station_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"},
        )

    raise HTTPException(status_code=400, detail=f"Format '{request.format}' not yet implemented")


# ---------------------------------------------------------------------------
# Hourly Aggregation
# ---------------------------------------------------------------------------
import math

# Fields to aggregate with mean & std dev
AGGREGATION_FIELDS = [
    "temperature", "humidity", "velocity",
    "outtake_temperature", "outtake_humidity", "outtake_velocity",
    "power", "current", "voltage",
]

# AWH device duct cross-sectional area (m²)
AWH_DUCT_AREA_M2 = 0.18


def _compute_absolute_humidity(temp_c: float, rh_pct: float) -> float:
    """Compute absolute humidity (g/m³) from temperature (°C) and relative humidity (%)."""
    # Magnus formula for saturation vapor pressure
    es = 6.112 * math.exp((17.67 * temp_c) / (temp_c + 243.5))
    ah = (216.7 * (rh_pct / 100.0) * es) / (273.15 + temp_c)
    return round(ah, 4)


def _velocity_to_mps(velocity: float, unit: Optional[str]) -> float:
    """Normalize velocity to m/s for physics-based calculations."""
    u = (unit or "").lower()
    if u == "km/h":
        return velocity / 3.6
    if u == "mph":
        return velocity / 2.23694
    if u == "ft/s":
        return velocity / 3.28084
    if u == "ft/m":
        return velocity / 196.850394
    return velocity


def _compute_hourly_aggregation_sync(
    station_name: str,
    start_date: Optional[datetime],
    end_date: Optional[datetime],
) -> Optional[dict]:
    """Blocking: fetch readings from Firestore and compute hourly aggregates.

    Runs inside run_in_threadpool. Both the paginated Firestore fetch and the
    per-reading aggregation below are synchronous/CPU-bound; calling this
    directly from the async route would block the single event loop for the
    whole computation (which can run over 100k+ readings for wide date
    ranges), stalling every other request the server is handling.

    Returns None if no readings were found for the range.
    """
    readings_ref = (
        db.collection(settings.firestore_collection)
        .document(station_name)
        .collection("readings")
        .order_by("timestamp", direction=firestore.Query.ASCENDING)
    )
    if start_date:
        readings_ref = readings_ref.where("timestamp", ">=", start_date)
    if end_date:
        readings_ref = readings_ref.where("timestamp", "<=", end_date)

    # Paginate through all readings — single .limit() caps at ~7 days at 1 reading/minute
    raw = []
    page_query = readings_ref.limit(settings.max_query_limit)
    while True:
        batch = list(page_query.stream())
        if not batch:
            break
        raw.extend(_firestore_doc_to_dict(d.to_dict()) for d in batch)
        if len(batch) < settings.max_query_limit:
            break
        page_query = readings_ref.start_after(batch[-1]).limit(settings.max_query_limit)

    if not raw:
        return None

    # Group by hour bucket
    from collections import defaultdict
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in raw:
        ts = r.get("timestamp", "")
        if isinstance(ts, str) and len(ts) >= 13:
            hour_key = ts[:13] + ":00:00Z"  # e.g. "2026-03-10T15:00:00Z"
        else:
            continue
        buckets[hour_key].append(r)

    hourly_rows = []
    sorted_hours = sorted(buckets.keys())

    for hour_key in sorted_hours:
        readings_in_hour = buckets[hour_key]
        row: dict = {"hour": hour_key, "reading_count": len(readings_in_hour)}

        # Mean & std dev for standard fields
        for field in AGGREGATION_FIELDS:
            values = [r[field] for r in readings_in_hour if r.get(field) is not None and isinstance(r.get(field), (int, float))]
            if values:
                mean_val = sum(values) / len(values)
                row[f"{field}_mean"] = round(mean_val, 4)
                if len(values) > 1:
                    variance = sum((v - mean_val) ** 2 for v in values) / (len(values) - 1)
                    row[f"{field}_std"] = round(math.sqrt(variance), 4)
                else:
                    row[f"{field}_std"] = 0.0
            else:
                row[f"{field}_mean"] = None
                row[f"{field}_std"] = None

        # Absolute humidity (intake)
        ah_intake_vals = []
        for r in readings_in_hour:
            t = r.get("temperature")
            h = r.get("humidity")
            if isinstance(t, (int, float)) and isinstance(h, (int, float)):
                ah_intake_vals.append(_compute_absolute_humidity(t, h))
        if ah_intake_vals:
            mean_ah = sum(ah_intake_vals) / len(ah_intake_vals)
            row["abs_humidity_intake_mean"] = round(mean_ah, 4)
            row["abs_humidity_intake_std"] = round(
                math.sqrt(sum((v - mean_ah) ** 2 for v in ah_intake_vals) / max(len(ah_intake_vals) - 1, 1)), 4
            )
        else:
            row["abs_humidity_intake_mean"] = None
            row["abs_humidity_intake_std"] = None

        # Absolute humidity (outtake)
        ah_outtake_vals = []
        for r in readings_in_hour:
            t = r.get("outtake_temperature")
            h = r.get("outtake_humidity")
            if isinstance(t, (int, float)) and isinstance(h, (int, float)):
                ah_outtake_vals.append(_compute_absolute_humidity(t, h))
        if ah_outtake_vals:
            mean_ah_o = sum(ah_outtake_vals) / len(ah_outtake_vals)
            row["abs_humidity_outtake_mean"] = round(mean_ah_o, 4)
            row["abs_humidity_outtake_std"] = round(
                math.sqrt(sum((v - mean_ah_o) ** 2 for v in ah_outtake_vals) / max(len(ah_outtake_vals) - 1, 1)), 4
            )
        else:
            row["abs_humidity_outtake_mean"] = None
            row["abs_humidity_outtake_std"] = None

        # Water produced per hour: sum of positive weight increments (never subtract)
        weights = [(r.get("timestamp", ""), r.get("weight")) for r in readings_in_hour
                    if isinstance(r.get("weight"), (int, float))]
        if len(weights) >= 2:
            weights.sort(key=lambda x: x[0])
            water_produced = sum(
                max(weights[i][1] - weights[i - 1][1], 0)
                for i in range(1, len(weights))
            )
            row["water_produced_g"] = round(water_produced, 4)
        else:
            row["water_produced_g"] = None

        # Energy consumed per hour (delta energy, already stored in kWh by all
        # station readers — see CLAUDE.md data model)
        energies = [(r.get("timestamp", ""), r.get("energy")) for r in readings_in_hour
                     if isinstance(r.get("energy"), (int, float))]
        if len(energies) >= 2:
            energies.sort(key=lambda x: x[0])
            energy_kwh = energies[-1][1] - energies[0][1]
            row["energy_consumed_kWh"] = round(energy_kwh, 4)
        else:
            row["energy_consumed_kWh"] = None

        # Energy per liter (kWh/L)
        water_g = row.get("water_produced_g")
        energy_kwh_val = row.get("energy_consumed_kWh")
        if water_g and energy_kwh_val and water_g > 0:
            water_L = water_g / 1000.0  # grams to liters (assuming water density ~ 1 g/mL)
            row["energy_per_liter_kWh_L"] = round(energy_kwh_val / water_L, 4) if water_L > 0 else None
            row["water_produced_L"] = round(water_L, 6)
        else:
            row["energy_per_liter_kWh_L"] = None
            row["water_produced_L"] = row.get("water_produced_g", None) and round(row["water_produced_g"] / 1000.0, 6)

        # Hourly harvesting efficiency (%):
        # (sum of positive weight deltas) / (sum of intake-available water in same intervals) * 100
        sorted_hour = sorted(readings_in_hour, key=lambda r: r.get("timestamp", ""))
        captured_g = 0.0
        intake_available_g = 0.0
        for i in range(1, len(sorted_hour)):
            prev_r = sorted_hour[i - 1]
            cur_r = sorted_hour[i]

            prev_w = prev_r.get("weight")
            cur_w = cur_r.get("weight")
            if isinstance(prev_w, (int, float)) and isinstance(cur_w, (int, float)):
                captured_g += max(cur_w - prev_w, 0)

            t = cur_r.get("temperature")
            h = cur_r.get("humidity")
            v = cur_r.get("velocity")
            unit = cur_r.get("unit")

            if not (isinstance(t, (int, float)) and isinstance(h, (int, float)) and isinstance(v, (int, float))):
                continue
            if h <= 0 or v <= 0:
                continue

            abs_h = _compute_absolute_humidity(t, h)
            vel_mps = _velocity_to_mps(v, unit if isinstance(unit, str) else None)
            if abs_h <= 0 or vel_mps <= 0:
                continue

            try:
                dt_ms = (
                    datetime.fromisoformat(cur_r.get("timestamp").replace("Z", "+00:00"))
                    - datetime.fromisoformat(prev_r.get("timestamp").replace("Z", "+00:00"))
                ).total_seconds() * 1000
            except Exception:
                dt_ms = 30000

            dt_s = min(max(dt_ms / 1000.0, 0.0), 120.0)
            intake_available_g += abs_h * vel_mps * AWH_DUCT_AREA_M2 * dt_s

        row["intake_available_water_g_hourly"] = round(intake_available_g, 4) if intake_available_g > 0 else None
        row["water_captured_g_hourly"] = round(captured_g, 4) if captured_g > 0 else 0.0
        if intake_available_g > 0:
            row["harvesting_efficiency_pct_hourly"] = round(min((captured_g / intake_available_g) * 100.0, 100.0), 4)
        else:
            row["harvesting_efficiency_pct_hourly"] = 0.0

        hourly_rows.append(row)

    return {
        "station_name": station_name,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "total_hours": len(hourly_rows),
        "data": hourly_rows,
    }


@app.get("/stations/{station_name}/hourly", tags=["Analytics"])
async def get_hourly_aggregation(
    station_name: str,
    start_date: Optional[datetime] = Query(None, description="Start date (ISO format)"),
    end_date: Optional[datetime] = Query(None, description="End date (ISO format)"),
):
    """
    Compute hourly aggregated statistics for a station.

    Returns per-hour buckets with:
    - mean & std dev for temperature, humidity, velocity (intake & outtake), power
    - absolute humidity (intake & outtake) mean & std dev
    - water_produced_L (delta weight per hour)
    - energy_consumed_kWh (delta energy per hour)
    - energy_per_liter (kWh/L)
    - harvesting_efficiency_pct_hourly (ratio of captured water vs intake-available water over the hour)
    """
    if not db:
        raise HTTPException(status_code=503, detail="Firestore not initialised")

    # Cache hourly aggregations for 10 minutes (they are expensive to compute)
    hourly_cache_key = f"hourly:{station_name}:{start_date}:{end_date}"
    cached_hourly = cache.get(hourly_cache_key)
    if cached_hourly:
        return cached_hourly

    result = await run_in_threadpool(_compute_hourly_aggregation_sync, station_name, start_date, end_date)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No readings found for '{station_name}' in the given range")

    cache.set(hourly_cache_key, result, ttl=600)
    return result


# ---------------------------------------------------------------------------
# Station Registry (for RPi UI dropdown & ingestion validation)
# ---------------------------------------------------------------------------
@app.get("/stations-registry", response_model=StationRegistryResponse, tags=["Registry"])
async def get_stations_registry(
    status: Optional[str] = Query(None, description="Filter by status: ACTIVE, INACTIVE, PENDING")
):
    """Return the list of known stations from Firestore with their online/offline status.

    Used by:
    - RPi control panel to populate the station selector dropdown
    - Ingestion worker to validate incoming measurements
    """
    if not db:
        raise HTTPException(status_code=503, detail="Firestore not initialised")

    stations_ref = db.collection(settings.firestore_collection)
    station_docs = list(stations_ref.list_documents())

    def _classify(sdoc) -> StationRegistryItem:
        sname = sdoc.id
        # Peek at the latest reading to determine online/offline
        reading_docs = list(
            stations_ref.document(sname)
            .collection("readings")
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(1)
            .stream()
        )

        if not reading_docs:
            station_status = "PENDING"
        else:
            last_ts_raw = reading_docs[0].to_dict().get("timestamp")
            station_status = "INACTIVE"
            if last_ts_raw:
                try:
                    last_dt = datetime.fromisoformat(last_ts_raw) if isinstance(last_ts_raw, str) else last_ts_raw
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600 <= 48:
                        station_status = "ACTIVE"
                except Exception:
                    pass

        return StationRegistryItem(
            station_name=sname,
            location=sname.split("@", 1)[1].strip() if "@" in sname else None,
            status=station_status,
        )

    with ThreadPoolExecutor(max_workers=min(len(station_docs), 16)) as executor:
        items = list(executor.map(_classify, station_docs))

    if status:
        items = [s for s in items if s.status == status.upper()]

    items.sort(key=lambda s: s.station_name)
    return StationRegistryResponse(stations=items, total=len(items))


@app.post("/stations-registry", response_model=StationRegistryItem, status_code=201, tags=["Registry"])
async def create_station(payload: CreateStationRequest):
    """Register a new station in Firestore.

    Called by the RPi control panel when an operator sets up a new device.
    The station is created with status PENDING (no readings yet).
    Returns 409 Conflict if a station with that name already exists.
    """
    if not db:
        raise HTTPException(status_code=503, detail="Firestore not initialised")

    station_name = payload.station_name.strip()
    if not station_name:
        raise HTTPException(status_code=422, detail="station_name must not be empty")

    doc_ref = db.collection(settings.firestore_collection).document(station_name)
    if doc_ref.get().exists:
        raise HTTPException(status_code=409, detail=f"Station '{station_name}' already exists")

    location = payload.location
    if location is None and "@" in station_name:
        location = station_name.split("@", 1)[1].strip()

    doc_ref.set({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "PENDING",
        "location": location or "",
    })

    return StationRegistryItem(
        station_name=station_name,
        location=location,
        status="PENDING",
    )


@app.get("/cache/stats", tags=["Cache"])
async def get_cache_stats():
    return cache.get_stats()


@app.post("/cache/invalidate", tags=["Cache"])
async def invalidate_cache(station_name: Optional[str] = None):
    invalidate_station_cache(station_name)
    return {
        "status": "success",
        "message": f"Cache invalidated for {'station: ' + station_name if station_name else 'all stations'}",
    }


@app.post("/cache/flush", tags=["Cache"])
async def flush_cache():
    success = cache.flush_all()
    if success:
        return {"status": "success", "message": "All cache data flushed"}
    raise HTTPException(status_code=500, detail="Failed to flush cache")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
    )
