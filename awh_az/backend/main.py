from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from typing import List, Optional
from datetime import datetime
import json
import io
import csv

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

# Initialize FastAPI app
app = FastAPI(
    title="AWH Station Monitoring API",
    description="API for monitoring Atmospheric Water Harvesting stations with dynamic schema support",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# In-memory data store (will be replaced with Firestore/Redis)
# This loads our test data for initial development
def load_test_data():
    """Load test data from JSON files"""
    import os
    data = []
    sample_dir = os.path.join(os.path.dirname(__file__), "..", "water-station-dashboard", "sample-data")
    
    for filename in ["test-station-1.json", "test-station-2.json"]:
        filepath = os.path.join(sample_dir, filename)
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                readings = json.load(f)
                data.extend(readings)
    
    return data


READINGS_DATA = []


@app.on_event("startup")
async def startup_event():
    """Load data on startup"""
    global READINGS_DATA
    READINGS_DATA = load_test_data()
    print(f"✅ Loaded {len(READINGS_DATA)} test readings")


@app.get("/", tags=["Health"])
async def root():
    """Root endpoint"""
    return {
        "message": "AWH Station Monitoring API",
        "version": "1.0.0",
        "docs": "/docs"
    }


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Health check endpoint"""
    redis_status = "online" if cache.health_check() else "offline"
    
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(),
        services={
            "api": "online",
            "redis": redis_status,
            "firestore": "not_configured"
        }
    )


@app.get("/stations", response_model=List[StationInfo], tags=["Stations"])
async def get_stations():
    """
    Get list of all stations with metadata.
    Returns station configurations and available fields per station.
    """
    # Try to get from cache first
    cache_key = get_stations_cache_key()
    cached_data = cache.get(cache_key)
    if cached_data:
        print(f"✅ Cache HIT: {cache_key}")
        return cached_data
    
    print(f"❌ Cache MISS: {cache_key}")
    
    # Group readings by station
    stations_dict = {}
    
    for reading in READINGS_DATA:
        station_name = reading.get("station_name")
        if station_name not in stations_dict:
            stations_dict[station_name] = {
                "readings": [],
                "fields": set()
            }
        
        stations_dict[station_name]["readings"].append(reading)
        
        # Track available fields (non-null values)
        for key, value in reading.items():
            if value is not None and key not in ["station_name", "timestamp"]:
                stations_dict[station_name]["fields"].add(key)
    
    # Build response
    stations = []
    for station_name, data in stations_dict.items():
        readings = data["readings"]
        available_fields = list(data["fields"])
        
        # Group fields by category
        field_groups = {
            "intake_air": [f for f in available_fields if f in ["temperature", "humidity", "velocity"]],
            "outtake_air": [f for f in available_fields if f.startswith("outtake_")],
            "water_production": [f for f in available_fields if f in ["flow_lmin", "flow_hz", "weight"]],
            "power": [f for f in available_fields if f in ["power", "voltage", "current", "energy"]],
            "system": [f for f in available_fields if f in ["pump_status", "unit"]]
        }
        
        # Get latest reading
        latest = max(readings, key=lambda x: x.get("timestamp", ""))
        
        metadata = StationMetadata(
            station_name=station_name,
            available_fields=available_fields,
            field_groups=field_groups,
            last_reading=latest.get("timestamp"),
            total_readings=len(readings)
        )
        
        station_info = StationInfo(
            station_name=station_name,
            unit=latest.get("unit", "Unknown"),
            location=latest.get("location"),
            status="active",
            metadata=metadata
        )
        
        stations.append(station_info)
    
    # Cache the result
    cache.set(cache_key, [s.dict() for s in stations], ttl=300)
    
    return stations


@app.get("/stations/{station_name}/readings", response_model=ReadingsResponse, tags=["Readings"])
async def get_station_readings(
    station_name: str,
    start_date: Optional[datetime] = Query(None, description="Start date filter (ISO format)"),
    end_date: Optional[datetime] = Query(None, description="End date filter (ISO format)"),
    fields: Optional[str] = Query(None, description="Comma-separated list of fields to include"),
    limit: int = Query(100, le=10000, description="Maximum number of records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip")
):
    """
    Get readings for a specific station with filtering and pagination.
    Supports date range filtering and field selection.
    """
    # Try to get from cache first
    cache_key = get_station_readings_cache_key(
        station_name, limit, offset,
        start_date.isoformat() if start_date else None,
        end_date.isoformat() if end_date else None,
        fields
    )
    cached_data = cache.get(cache_key)
    if cached_data:
        print(f"✅ Cache HIT: {cache_key}")
        return cached_data
    
    print(f"❌ Cache MISS: {cache_key}")
):
    """
    Get readings for a specific station with filtering and pagination.
    Supports date range filtering, field selection, and pagination.
    """
    # Filter by station
    filtered_data = [r for r in READINGS_DATA if r.get("station_name") == station_name]
    
    if not filtered_data:
        raise HTTPException(status_code=404, detail=f"Station '{station_name}' not found")
    
    # Filter by date range
    if start_date:
        filtered_data = [r for r in filtered_data if datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) >= start_date]
    
    if end_date:
        filtered_data = [r for r in filtered_data if datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) <= end_date]
    
    # Sort by timestamp
    filtered_data.sort(key=lambda x: x.get("timestamp", ""))
    
    total = len(filtered_data)
    
    # Pagination
    paginated_data = filtered_data[offset:offset + limit]
    
    # Field filtering
    if fields:
        requested_fields = [f.strip() for f in fields.split(",")]
        requested_fields.extend(["station_name", "timestamp", "unit"])  # Always include required fields
        
        paginated_data = [
            {k: v for k, v in reading.items() if k in requested_fields}
            for reading in paginated_data
        ]
    
    # Build metadata
    available_fields = set()
    for reading in filtered_data:
        for key, value in reading.items():
            if value is not None and key not in ["station_name", "timestamp"]:
                available_fields.add(key)
    
    metadata = StationMetadata(
        station_name=station_name,
        available_fields=list(available_fields),
        field_groups={},
        total_readings=total
    )
    
    # Convert to Pydantic models
    readings = [StationReading(**r) for r in paginated_data]
    
    response = ReadingsResponse(
        data=readings,
        total=total,
        limit=limit,
        offset=offset,
        metadata=metadata
    )
    
    # Cache the result
    cache.set(cache_key, response.dict(), ttl=180)  # Cache for 3 minutes
    
    return response


@app.post("/export", tags=["Export"])
async def export_data(request: BulkExportRequest):
    """
    Export station data in CSV, JSON, or Parquet format.
    Supports station filtering, date range, and field selection.
    """
    # Filter data based on request
    filtered_data = READINGS_DATA.copy()
    
    if request.station_names:
        filtered_data = [r for r in filtered_data if r.get("station_name") in request.station_names]
    
    if request.start_date:
        filtered_data = [r for r in filtered_data if datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) >= request.start_date]
    
    if request.end_date:
        filtered_data = [r for r in filtered_data if datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) <= request.end_date]
    
    if not filtered_data:
        raise HTTPException(status_code=404, detail="No data found matching the criteria")
    
    # Field filtering
    if request.fields:
        filtered_data = [
            {k: v for k, v in reading.items() if k in request.fields or k in ["station_name", "timestamp"]}
            for reading in filtered_data
        ]
    
    # Export based on format
    if request.format == "csv":
        output = io.StringIO()
        
        # Get all possible fields
        all_fields = set()
        for reading in filtered_data:
            all_fields.update(reading.keys())
        
        fieldnames = sorted(list(all_fields))
        
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(filtered_data)
        
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=station_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"}
        )
    
    elif request.format == "json":
        json_data = json.dumps(filtered_data, indent=2, default=str)
        return StreamingResponse(
            io.BytesIO(json_data.encode()),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=station_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"}
        )
    
    else:
        raise HTTPException(status_code=400, detail=f"Format '{request.format}' not yet implemented")


@app.get("/cache/stats", tags=["Cache"])
async def get_cache_stats():
    """
    Get Redis cache statistics and health information.
    """
    return cache.get_stats()


@app.post("/cache/invalidate", tags=["Cache"])
async def invalidate_cache(station_name: Optional[str] = None):
    """
    Invalidate cache for a specific station or all stations.
    
    Args:
        station_name: Optional station name to invalidate. If None, invalidates all.
    """
    invalidate_station_cache(station_name)
    
    return {
        "status": "success",
        "message": f"Cache invalidated for {'station: ' + station_name if station_name else 'all stations'}"
    }


@app.post("/cache/flush", tags=["Cache"])
async def flush_cache():
    """
    Flush all cache data (use with caution!).
    Requires admin privileges in production.
    """
    success = cache.flush_all()
    
    if success:
        return {
            "status": "success",
            "message": "All cache data flushed"
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to flush cache")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload
    )
