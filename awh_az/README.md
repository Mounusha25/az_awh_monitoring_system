# Arizona Atmospheric Water Harvesting (AzAWH) Dashboard

Full-stack monitoring platform for atmospheric water harvesting stations with dynamic schema support for heterogeneous sensor data.

## 🎯 Project Overview

This system addresses the challenge of monitoring multiple water harvesting stations with **variable sensor configurations**. Not all stations have the same equipment, resulting in different data schemas across the network. The platform handles this gracefully with:

- **Dynamic schema detection** - Automatically discovers available fields per station
- **Flexible data models** - All sensor fields are optional, no validation errors for missing data
- **Real-time monitoring** - Track temperature, humidity, flow rates, power consumption, and more
- **Data export** - CSV/JSON export with field selection and date filtering
- **Professional UI** - ASU-branded interface with interactive charts

## 🏗️ Architecture

```
awh_az/
├── water-station-dashboard/      # Next.js 14 frontend
│   ├── src/
│   │   ├── app/                  # App Router pages
│   │   │   ├── page.tsx          # Homepage with stations grid
│   │   │   └── stations/[id]/   # Station details & analytics
│   │   ├── components/           # Reusable React components
│   │   └── data/                 # Constants & config
│   └── sample-data/              # Test JSON files
│       ├── test-station-1.json   # AguaPars unit (has flow sensors)
│       └── test-station-2.json   # Airjoule unit (has voltage/current)
│
└── backend/                       # FastAPI backend
    ├── main.py                    # API endpoints & app config
    ├── models.py                  # Pydantic models with dynamic schemas
    ├── config.py                  # Settings & environment config
    ├── requirements.txt           # Python dependencies
    └── README.md                  # Backend documentation
```

## 🚀 Quick Start

### Frontend (Next.js)

```bash
cd water-station-dashboard
npm install
npm run dev
```

Visit: **http://localhost:3000**

### Backend (FastAPI)

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

API docs: **http://localhost:8000/docs**

## 📊 Features

### Current (v1.0)
- ✅ **Station List Page** - View all stations with status indicators
- ✅ **Station Details** - Individual station monitoring with charts
- ✅ **Multi-Parameter Selection** - Compare up to 2 parameters simultaneously
- ✅ **Date Range Filtering** - Query historical data
- ✅ **Data Export** - CSV download with field selection
- ✅ **RESTful API** - FastAPI backend with dynamic schema support
- ✅ **Field Discovery** - Automatic metadata generation per station
- ✅ **Responsive Design** - Professional ASU-themed interface

### Roadmap (v2.0)
- 🔄 **Redis Caching** - Query optimization for frequent requests
- 🔄 **Firestore Integration** - Production data source
- 🔄 **WebSocket Updates** - Real-time data push
- 🔄 **Authentication** - JWT-based user auth
- 🔄 **Data Pipeline** - Firestore → GCS → Parquet → BigQuery
- 🔄 **Advanced Analytics** - Trend analysis, anomaly detection
- 🔄 **Alert System** - Email/SMS notifications for thresholds

## 🔧 Technology Stack

### Frontend
- **Next.js 16** - React framework with App Router
- **TypeScript 5** - Type safety
- **Material-UI v7** - Component library
- **Recharts v3** - Data visualization
- **Framer Motion v12** - Animations
- **PapaParse v5** - CSV export

### Backend
- **FastAPI 0.115** - Modern Python API framework
- **Pydantic 2.10** - Data validation with dynamic schemas
- **Uvicorn** - ASGI server with auto-reload
- **Redis 5.2** - Caching layer (planned)
- **Google Cloud Firestore 2.19** - Document database (planned)
- **Pandas 2.2** - Data processing
- **PyArrow 18.1** - Parquet export (planned)

## 📡 API Endpoints

### Health Check
```bash
GET /health
```

### List All Stations
```bash
GET /stations
```
Returns stations with metadata (available fields, last reading, total count).

### Get Station Readings
```bash
GET /stations/{station_name}/readings
  ?start_date=2025-11-01T00:00:00Z
  &end_date=2025-11-02T00:00:00Z
  &fields=temperature,humidity,power
  &limit=100
  &offset=0
```

### Export Data
```bash
POST /export
{
  "station_names": ["test-station-1", "test-station-2"],
  "start_date": "2025-11-01T00:00:00Z",
  "end_date": "2025-11-02T00:00:00Z",
  "fields": ["temperature", "humidity", "power"],
  "format": "csv"
}
```

## 🗃️ Data Schema

### Common Fields (all stations)
```typescript
{
  station_name: string      // Station identifier
  timestamp: datetime       // ISO 8601 format
  unit: string             // Equipment type (AguaPars, Airjoule, etc.)
  temperature: number      // Intake air temp (°C)
  humidity: number         // Intake air humidity (%)
  velocity: number         // Intake air velocity (m/s)
  outtake_humidity: number // Outtake air humidity (%)
  outtake_velocity: number // Outtake air velocity (m/s)
  weight: number           // Water weight (g)
  power: number            // Power consumption (W)
  energy: number           // Energy consumed (Wh)
  pump_status: string      // ON/OFF
}
```

### Variable Fields (station-dependent)
```typescript
{
  // AguaPars units have:
  flow_lmin?: number       // Flow rate (L/min)
  flow_hz?: number         // Flow frequency (Hz)
  
  // Airjoule units have:
  voltage?: number         // Input voltage (V)
  current?: number         // Input current (A)
}
```

## 🎨 Design System

### Color Palette
- **Maroon**: `#901340` - Primary brand color
- **Golden**: `#ffcb25` - Secondary brand color  
- **Water Blue**: `#1e88e5` - Station theme
- **Dark Text**: `#191919` - Headings
- **Body Text**: `#2b2b2b` - Body copy
- **Secondary Text**: `#484848` - Metadata

### Typography
- **Headings**: 700 weight, -0.5px letter-spacing
- **Body**: 400 weight, 0.5px letter-spacing
- **Stats**: 2.5rem size, 700 weight

## 🧪 Testing

### Backend Tests
```bash
# Test stations endpoint
curl http://localhost:8000/stations

# Test specific station with field filtering
curl "http://localhost:8000/stations/test-station-1/readings?fields=temperature,power&limit=5"

# Test export
curl -X POST http://localhost:8000/export \
  -H "Content-Type: application/json" \
  -d '{"station_names": ["test-station-1"], "format": "csv"}' \
  --output export.csv
```

### Frontend
```bash
cd water-station-dashboard
npm run build    # Production build
npm run lint     # ESLint check
```

## 📝 Development Notes

### Handling Heterogeneous Data

The key challenge is that stations have **different sensor suites**, creating variable JSON schemas. Our solution:

1. **Pydantic Models**: All sensor fields are `Optional[type] = None`
   - No validation errors for missing fields
   - Type safety for fields that exist
   - Clean serialization with null values

2. **Metadata Discovery**: `/stations` endpoint analyzes actual data to report available fields per station
   - Frontend adapts UI based on metadata
   - Charts only render available parameters
   - Export includes only requested fields

3. **Field Filtering**: API supports `?fields=` parameter
   - Always includes required fields (station_name, timestamp, unit)
   - Reduces payload size for large queries
   - Supports comma-separated field lists

### Test Data

Two sample stations demonstrate the heterogeneous challenge:

- **test-station-1** (AguaPars): Has `flow_lmin`, `flow_hz`; lacks `voltage`, `current`
- **test-station-2** (Airjoule): Has `voltage`, `current`; lacks `flow_lmin`, `flow_hz`

Both share 13 common fields, differing in 4 optional fields.

## 🤝 Contributing

### Adding New Fields

1. **Update Pydantic Model** (`backend/models.py`):
```python
class StationReading(BaseModel):
    new_field: Optional[float] = None  # Always Optional!
```

2. **Update Frontend Constants** (if needed for display):
```typescript
// src/data/constants.ts
export const FIELD_UNITS = {
  new_field: "unit"
};
```

3. **Update Test Data** (`sample-data/*.json`):
```json
{
  "new_field": 123.45
}
```

### Adding New Stations

Simply add a new JSON file to `sample-data/` with the station's available fields. The system automatically discovers and adapts.

## 📧 Contact

For questions or issues, please contact the AzAWH development team.

## 📄 License

Internal ASU project - All rights reserved.
