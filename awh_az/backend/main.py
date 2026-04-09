from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from typing import List, Optional
from datetime import datetime, timezone
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
    HealthResponse
)
from config import settings
from cache import cache, get_stations_cache_key, get_station_readings_cache_key, invalidate_station_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Firebase initialization
# ---------------------------------------------------------------------------
db = None


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
app = FastAPI(
    title="AWH Station Monitoring API",
    description="API for monitoring Atmospheric Water Harvesting stations — backed by Firestore",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.all_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    init_firestore()
    if db:
        print("✅ Firestore initialised – ready to serve")
    else:
        print("⚠️  Firestore NOT initialised – check serviceAccountKey.json")


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
    # Use list_documents() instead of stream() to find phantom parent docs
    # (stations that have sub-collections but no document data)
    station_docs = stations_ref.list_documents()

    stations: list[StationInfo] = []

    for sdoc in station_docs:
        station_name = sdoc.id

        # Fetch last 50 readings to derive metadata
        readings_ref = (
            stations_ref.document(station_name)
            .collection("readings")
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(50)
        )
        reading_docs = list(readings_ref.stream())

        if not reading_docs:
            continue

        # Derive available fields from sampled readings
        available_fields: set[str] = set()
        for rdoc in reading_docs:
            data = rdoc.to_dict()
            for key, val in data.items():
                if val is not None and key not in SKIP_FIELDS:
                    available_fields.add(key)

        available_list = sorted(available_fields)
        field_groups = _build_field_groups(available_list)

        latest = _firestore_doc_to_dict(reading_docs[0].to_dict())

        # Count total readings (use aggregation if available, else estimate)
        total_count = len(reading_docs)  # approximate from sample

        metadata = StationMetadata(
            station_name=station_name,
            available_fields=available_list,
            field_groups=field_groups,
            last_reading=latest.get("timestamp"),
            total_readings=total_count,
        )

        stations.append(
            StationInfo(
                station_name=station_name,
                unit=latest.get("unit", "Unknown"),
                location=latest.get("location"),
                status="active",
                metadata=metadata,
            )
        )

    cache.set(cache_key, [s.dict() for s in stations], ttl=300)
    return stations


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

    readings_ref = (
        db.collection(settings.firestore_collection)
        .document(station_name)
        .collection("readings")
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
    )

    if start_date:
        readings_ref = readings_ref.where("timestamp", ">=", start_date)
    if end_date:
        readings_ref = readings_ref.where("timestamp", "<=", end_date)

    # Fetch with offset + limit (Firestore doesn't support offset natively)
    fetch_limit = offset + limit
    docs = list(readings_ref.limit(fetch_limit).stream())

    if not docs:
        raise HTTPException(status_code=404, detail=f"Station '{station_name}' not found or has no readings")

    # Apply offset
    paged_docs = docs[offset:]

    # Parse readings
    requested_fields = None
    if fields:
        requested_fields = set(f.strip() for f in fields.split(","))
        requested_fields.update({"station_name", "timestamp", "unit"})

    readings_list: list[dict] = []
    available_fields: set[str] = set()

    for rdoc in paged_docs:
        data = _firestore_doc_to_dict(rdoc.to_dict())

        for key, val in data.items():
            if val is not None and key not in SKIP_FIELDS:
                available_fields.add(key)

        if requested_fields:
            data = {k: v for k, v in data.items() if k in requested_fields}

        readings_list.append(data)

    total = len(docs)

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


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@app.post("/export", tags=["Export"])
async def export_data(request: BulkExportRequest):
    """Export station data in CSV or JSON format."""
    if not db:
        raise HTTPException(status_code=503, detail="Firestore not initialised")

    all_readings: list[dict] = []

    # Determine which stations to query
    if request.station_names:
        station_names = request.station_names
    else:
        station_names = [
            doc.id
            for doc in db.collection(settings.firestore_collection).stream()
        ]

    for sname in station_names:
        query = (
            db.collection(settings.firestore_collection)
            .document(sname)
            .collection("readings")
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
        )
        if request.start_date:
            query = query.where("timestamp", ">=", request.start_date)
        if request.end_date:
            query = query.where("timestamp", "<=", request.end_date)

        for rdoc in query.limit(settings.max_query_limit).stream():
            data = _firestore_doc_to_dict(rdoc.to_dict())
            if request.fields:
                data = {k: v for k, v in data.items() if k in request.fields or k in ("station_name", "timestamp")}
            all_readings.append(data)

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


def _compute_absolute_humidity(temp_c: float, rh_pct: float) -> float:
    """Compute absolute humidity (g/m³) from temperature (°C) and relative humidity (%)."""
    # Magnus formula for saturation vapor pressure
    es = 6.112 * math.exp((17.67 * temp_c) / (temp_c + 243.5))
    ah = (216.7 * (rh_pct / 100.0) * es) / (273.15 + temp_c)
    return round(ah, 4)


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
    """
    if not db:
        raise HTTPException(status_code=503, detail="Firestore not initialised")

    # Fetch readings
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

    docs = list(readings_ref.limit(settings.max_query_limit).stream())
    if not docs:
        raise HTTPException(status_code=404, detail=f"No readings found for '{station_name}' in the given range")

    raw = [_firestore_doc_to_dict(d.to_dict()) for d in docs]

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

        # Energy consumed per hour (delta energy in Wh → kWh)
        energies = [(r.get("timestamp", ""), r.get("energy")) for r in readings_in_hour
                     if isinstance(r.get("energy"), (int, float))]
        if len(energies) >= 2:
            energies.sort(key=lambda x: x[0])
            energy_delta_wh = energies[-1][1] - energies[0][1]
            energy_kwh = energy_delta_wh / 1000.0
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

        hourly_rows.append(row)

    return {
        "station_name": station_name,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "total_hours": len(hourly_rows),
        "data": hourly_rows,
    }


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------
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
