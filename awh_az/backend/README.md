# AWH Station Monitoring Backend

FastAPI backend for Atmospheric Water Harvesting station monitoring with dynamic schema support.

## Features

- ✅ **Dynamic Schema Handling**: Flexible Pydantic models support variable fields across stations
- ✅ **RESTful API**: Clean endpoints for stations list, readings query, data export
- ✅ **Field Filtering**: Query only the fields you need
- ✅ **Date Range Filtering**: Filter readings by timestamp
- ✅ **Pagination**: Handle large datasets efficiently
- ✅ **Metadata Discovery**: Automatic detection of available fields per station
- ✅ **Data Export**: CSV/JSON export with streaming support
- 🔄 **Redis Caching** (Coming soon)
- 🔄 **Firestore Integration** (Coming soon)
- 🔄 **WebSocket Real-time Updates** (Coming soon)

## Installation

1. **Create virtual environment**:
```bash
cd backend
python3 -m venv venv
source venv/bin/activate  # On macOS/Linux
```

2. **Install dependencies**:
```bash
pip install -r requirements.txt
```

3. **Configure environment**:
```bash
cp .env.example .env
# Edit .env with your configuration
```

## Running the API

**Development mode** (auto-reload):
```bash
python main.py
```

**Production mode**:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

The API will be available at:
- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## API Endpoints

### Health Check
```bash
GET /health
```

### Get All Stations
```bash
GET /stations
```
Returns list of stations with metadata (available fields, last reading, total count).

### Get Station Readings
```bash
GET /stations/{station_name}/readings?start_date=2025-11-01T00:00:00Z&end_date=2025-11-02T00:00:00Z&fields=temperature,humidity&limit=100&offset=0
```

**Query Parameters**:
- `start_date`: ISO format datetime (optional)
- `end_date`: ISO format datetime (optional)
- `fields`: Comma-separated field names (optional)
- `limit`: Max records to return (default: 100, max: 10000)
- `offset`: Pagination offset (default: 0)

### Export Data
```bash
POST /export
Content-Type: application/json

{
  "station_names": ["test-station-1"],
  "start_date": "2025-11-01T00:00:00Z",
  "end_date": "2025-11-02T00:00:00Z",
  "fields": ["temperature", "humidity", "power"],
  "format": "csv"
}
```

**Supported formats**: `csv`, `json`

## Project Structure

```
backend/
├── main.py              # FastAPI application & endpoints
├── models.py            # Pydantic models with dynamic schema support
├── config.py            # Configuration & settings
├── requirements.txt     # Python dependencies
├── .env.example         # Environment template
└── README.md           # This file
```

## Dynamic Schema Design

The `StationReading` model makes **all sensor fields optional**, allowing different stations to have different equipment:

```python
class StationReading(BaseModel):
    station_name: str  # Required
    timestamp: datetime  # Required
    
    # All sensor fields are Optional
    temperature: Optional[float] = None
    flow_lmin: Optional[float] = None
    voltage: Optional[float] = None
    # ... etc
```

This approach:
- ✅ Handles missing fields gracefully (no validation errors)
- ✅ Preserves type safety for fields that exist
- ✅ Works with Firestore's variable schemas
- ✅ Enables field-level metadata discovery

## Testing

**Test with curl**:
```bash
# Get all stations
curl http://localhost:8000/stations

# Get readings for test-station-1
curl "http://localhost:8000/stations/test-station-1/readings?limit=5"

# Export CSV
curl -X POST http://localhost:8000/export \
  -H "Content-Type: application/json" \
  -d '{"station_names": ["test-station-1"], "format": "csv"}' \
  --output data.csv
```

**Interactive testing**: Visit http://localhost:8000/docs

## Next Steps

1. **Add Redis caching** for frequent queries
2. **Integrate Firestore** for production data
3. **Implement WebSocket** for real-time updates
4. **Add authentication** (JWT tokens)
5. **Create data pipeline** (Firestore → GCS → Parquet)
6. **Add monitoring** (Prometheus/Grafana)

## Development Notes

Currently using test data from `../water-station-dashboard/sample-data/`. Replace `load_test_data()` with Firestore client for production.
